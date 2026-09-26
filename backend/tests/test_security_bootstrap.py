from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.cli import main
from app.tasks.market_data import HttpMarketDataClient, MarketDataClientError


class FakeResponse:
    def __init__(self, body: object, *, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self) -> object:
        return self._body


class RecordingHttpClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


class ScriptedMarketDataClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.sync_calls = 0
        self.closed = False

    async def sync_securities(self) -> object:
        self.sync_calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


class ClientFactory:
    def __init__(self, client: ScriptedMarketDataClient) -> None:
        self.client = client
        self.calls: list[dict[str, object]] = []

    def __call__(self, base_url: str, **kwargs: object) -> ScriptedMarketDataClient:
        self.calls.append({"base_url": base_url, **kwargs})
        return self.client


def cli_settings() -> SimpleNamespace:
    return SimpleNamespace(
        market_data_service_url="http://market-data-service:8001",
        internal_api_token="internal-secret",
    )


@pytest.mark.asyncio
async def test_sync_securities_uses_authenticated_internal_endpoint_and_envelope() -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": True,
                "data": {"operation": "security", "succeeded": 2, "failed": 0},
                "error": None,
            }
        )
    )
    client = HttpMarketDataClient(
        "http://market-data-service:8001/",
        internal_api_token="internal-secret",
        timeout_seconds=4,
        client=transport,
    )

    result = await client.sync_securities()

    assert result == {"operation": "security", "succeeded": 2, "failed": 0}
    assert transport.calls == [
        {
            "url": "http://market-data-service:8001/internal/sync/securities",
            "json": {},
            "headers": {"X-Internal-Token": "internal-secret"},
            "timeout": 4.0,
        }
    ]


@pytest.mark.asyncio
async def test_sync_securities_rejects_unsuccessful_envelope() -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": {"operation": "security", "succeeded": 1, "failed": 1},
                "error": {"code": "PARTIAL_SYNC_FAILURE", "message": "partial"},
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(MarketDataClientError, match="synchronization failed"):
        await client.sync_securities()


def test_bootstrap_securities_succeeds_only_with_persisted_securities(capsys) -> None:
    client = ScriptedMarketDataClient([{"succeeded": 3, "failed": 0}])
    factory = ClientFactory(client)

    exit_code = main(
        ["bootstrap-securities", "--timeout-seconds", "4"],
        settings=cli_settings(),
        market_data_client_factory=factory,
    )

    assert exit_code == 0
    assert client.closed is True
    assert factory.calls == [
        {
            "base_url": "http://market-data-service:8001",
            "internal_api_token": "internal-secret",
            "timeout_seconds": 4.0,
        }
    ]
    assert "3 securities" in capsys.readouterr().out


def test_bootstrap_securities_rejects_empty_result_and_closes_client(capsys) -> None:
    client = ScriptedMarketDataClient([{"succeeded": 0, "failed": 0}])

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
    )

    assert exit_code == 1
    assert client.closed is True
    assert "no securities" in capsys.readouterr().err.lower()


def test_bootstrap_securities_rejects_partial_failure(capsys) -> None:
    client = ScriptedMarketDataClient([{"succeeded": 1, "failed": 1}])

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
    )

    assert exit_code == 1
    assert client.closed is True
    assert "1 failed" in capsys.readouterr().err.lower()


def test_bootstrap_securities_hides_exception_secrets(capsys) -> None:
    client = ScriptedMarketDataClient([RuntimeError("token=do-not-print")])

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert client.closed is True
    assert "security bootstrap" in captured.err.lower()
    assert "failed" in captured.err.lower()
    assert "do-not-print" not in captured.out + captured.err


def test_bootstrap_securities_hides_market_data_error_details(capsys) -> None:
    client = ScriptedMarketDataClient(
        [MarketDataClientError("provider-token=do-not-print")]
    )

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "market data" in captured.err.lower()
    assert "do-not-print" not in captured.out + captured.err


def test_bootstrap_securities_handles_client_factory_failure_without_details(capsys) -> None:
    def fail_factory(*args: object, **kwargs: object) -> object:
        raise RuntimeError("internal-token=do-not-print")

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=fail_factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "security bootstrap" in captured.err.lower()
    assert "do-not-print" not in captured.out + captured.err


def test_bootstrap_securities_rejects_false_summary_status(capsys) -> None:
    client = ScriptedMarketDataClient([{"ok": False, "succeeded": 2, "failed": 0}])

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
    )

    assert exit_code == 1
    assert "failed" in capsys.readouterr().err.lower()


def test_bootstrap_securities_retries_until_success(capsys) -> None:
    client = ScriptedMarketDataClient(
        [
            MarketDataClientError("market data request timed out"),
            {"succeeded": 0, "failed": 0},
            {"succeeded": 2, "failed": 0},
        ]
    )
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    exit_code = main(
        [
            "bootstrap-securities",
            "--retry-until-success",
            "--retry-interval-seconds",
            "0.25",
        ],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
        sleep=record_sleep,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert client.sync_calls == 3
    assert delays == [0.25, 0.25]
    assert client.closed is True
    assert captured.err.lower().count("attempt") == 2


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--retry-interval-seconds", "0"),
        ("--timeout-seconds", "-1"),
        ("--retry-interval-seconds", "nan"),
        ("--timeout-seconds", "inf"),
    ],
)
def test_bootstrap_securities_requires_positive_timing_options(option, value) -> None:
    with pytest.raises(SystemExit) as error:
        main(["bootstrap-securities", option, value])

    assert error.value.code == 2
