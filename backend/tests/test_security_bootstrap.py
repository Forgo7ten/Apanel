from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.cli import main
from app.tasks.market_data import (
    HttpMarketDataClient,
    MarketDataClientError,
    PermanentMarketDataClientError,
    RetryableMarketDataClientError,
)


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


class SequencedHttpClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        del url, kwargs
        self.calls += 1
        return self.responses.pop(0)


class RaisingHttpClient:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        raise self.error


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

    with pytest.raises(PermanentMarketDataClientError, match="synchronization failed"):
        await client.sync_securities()


@pytest.mark.parametrize("error_code", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"])
@pytest.mark.asyncio
async def test_sync_securities_classifies_transient_error_envelope_as_retryable(
    error_code: str,
) -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": None,
                "error": {
                    "code": error_code,
                    "message": "provider token=https://secret.invalid/detail",
                },
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(RetryableMarketDataClientError) as raised:
        await client.sync_securities()

    assert str(raised.value) == "market data request temporarily unavailable"
    assert "secret.invalid" not in str(raised.value)


@pytest.mark.parametrize("error_code", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"])
@pytest.mark.asyncio
async def test_sync_securities_classifies_transient_item_error_as_retryable(
    error_code: str,
) -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": {
                    "operation": "security",
                    "total": 1,
                    "succeeded": 0,
                    "failed": 1,
                    "ok": False,
                    "items": [
                        {
                            "symbol": "*",
                            "status": "failed",
                            "fetched": 0,
                            "persisted": 0,
                            "error": {
                                "code": error_code,
                                "message": "provider token=https://secret.invalid/detail",
                            },
                        }
                    ],
                },
                "error": {
                    "code": "PARTIAL_SYNC_FAILURE",
                    "message": "provider response body should not be trusted",
                },
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(RetryableMarketDataClientError):
        await client.sync_securities()


@pytest.mark.asyncio
async def test_sync_securities_keeps_mixed_item_errors_permanent() -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": {
                    "items": [
                        {"error": {"code": "PROVIDER_TIMEOUT", "message": "ignored"}},
                        {"error": {"code": "INVALID_MARKET_DATA", "message": "ignored"}},
                    ]
                },
                "error": {"code": "PARTIAL_SYNC_FAILURE", "message": "ignored"},
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(PermanentMarketDataClientError):
        await client.sync_securities()


@pytest.mark.asyncio
async def test_sync_securities_keeps_permanent_top_level_error_permanent() -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": {
                    "items": [
                        {"error": {"code": "PROVIDER_TIMEOUT", "message": "ignored"}},
                    ]
                },
                "error": {
                    "code": "INVALID_INTERNAL_TOKEN",
                    "message": "token=secret",
                },
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(PermanentMarketDataClientError) as raised:
        await client.sync_securities()

    assert str(raised.value) == "market data synchronization failed"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("top_level_code", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"])
@pytest.mark.asyncio
async def test_sync_securities_keeps_transient_top_level_and_items_retryable(
    top_level_code: str,
) -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": {
                    "items": [
                        {"error": {"code": "PROVIDER_TIMEOUT", "message": "ignored"}},
                        {"error": {"code": "PROVIDER_UNAVAILABLE", "message": "ignored"}},
                    ]
                },
                "error": {"code": top_level_code, "message": "ignored"},
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(RetryableMarketDataClientError):
        await client.sync_securities()


@pytest.mark.asyncio
async def test_sync_securities_keeps_transient_top_level_with_permanent_item_permanent() -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": {
                    "items": [
                        {"error": {"code": "INVALID_MARKET_DATA", "message": "ignored"}},
                    ]
                },
                "error": {"code": "PROVIDER_UNAVAILABLE", "message": "ignored"},
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(PermanentMarketDataClientError):
        await client.sync_securities()


@pytest.mark.parametrize("include_top_level_error", [False, True])
@pytest.mark.asyncio
async def test_sync_securities_retries_transient_items_without_top_level_error(
    include_top_level_error: bool,
) -> None:
    body: dict[str, object] = {
        "success": False,
        "data": {
            "items": [
                {"error": {"code": "PROVIDER_TIMEOUT", "message": "ignored"}},
                {"error": {"code": "PROVIDER_UNAVAILABLE", "message": "ignored"}},
            ]
        },
    }
    if include_top_level_error:
        body["error"] = None
    transport = RecordingHttpClient(FakeResponse(body))
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(RetryableMarketDataClientError):
        await client.sync_securities()


def test_bootstrap_securities_does_not_sleep_for_permanent_top_level_error_with_transient_item(
    capsys,
) -> None:
    transport = SequencedHttpClient(
        [
            FakeResponse(
                {
                    "success": False,
                    "data": {
                        "items": [
                            {
                                "error": {
                                    "code": "PROVIDER_TIMEOUT",
                                    "message": "provider detail token=secret",
                                }
                            }
                        ]
                    },
                    "error": {
                        "code": "INVALID_INTERNAL_TOKEN",
                        "message": "token=secret",
                    },
                }
            )
        ]
    )
    delays: list[float] = []

    def factory(base_url: str, **kwargs: object) -> HttpMarketDataClient:
        return HttpMarketDataClient(base_url, client=transport, **kwargs)

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
        market_data_client_factory=factory,
        sleep=record_sleep,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert transport.calls == 1
    assert delays == []
    assert "secret" not in captured.out + captured.err


def test_bootstrap_securities_retries_transient_http_envelope(capsys) -> None:
    transport = SequencedHttpClient(
        [
            FakeResponse(
                {
                    "success": False,
                    "data": None,
                    "error": {
                        "code": "PROVIDER_UNAVAILABLE",
                        "message": "token=https://secret.invalid/provider",
                    },
                }
            ),
            FakeResponse(
                {
                    "success": True,
                    "data": {"operation": "security", "succeeded": 2, "failed": 0},
                    "error": None,
                }
            ),
        ]
    )
    delays: list[float] = []

    def factory(base_url: str, **kwargs: object) -> HttpMarketDataClient:
        return HttpMarketDataClient(base_url, client=transport, **kwargs)

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
        market_data_client_factory=factory,
        sleep=record_sleep,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert transport.calls == 2
    assert delays == [0.25]
    assert "secret.invalid" not in captured.out + captured.err


def test_bootstrap_securities_single_attempt_keeps_transient_http_failure_nonzero(capsys) -> None:
    transport = SequencedHttpClient(
        [
            FakeResponse(
                {
                    "success": False,
                    "data": None,
                    "error": {
                        "code": "PROVIDER_TIMEOUT",
                        "message": "response detail token=secret",
                    },
                }
            )
        ]
    )
    delays: list[float] = []

    def factory(base_url: str, **kwargs: object) -> HttpMarketDataClient:
        return HttpMarketDataClient(base_url, client=transport, **kwargs)

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    exit_code = main(
        ["bootstrap-securities"],
        settings=cli_settings(),
        market_data_client_factory=factory,
        sleep=record_sleep,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert transport.calls == 1
    assert delays == []
    assert "secret" not in captured.out + captured.err


@pytest.mark.parametrize(
    "error_code",
    [
        "PARTIAL_SYNC_FAILURE",
        "INVALID_INTERNAL_TOKEN",
        "INVALID_REQUEST",
        "UNKNOWN_PROVIDER_CODE",
    ],
)
@pytest.mark.asyncio
async def test_sync_securities_keeps_non_allowlisted_error_codes_permanent(
    error_code: str,
) -> None:
    transport = RecordingHttpClient(
        FakeResponse(
            {
                "success": False,
                "data": None,
                "error": {
                    "code": error_code,
                    "message": "provider temporarily unavailable token=secret",
                },
            }
        )
    )
    client = HttpMarketDataClient("http://market-data-service:8001", client=transport)

    with pytest.raises(PermanentMarketDataClientError) as raised:
        await client.sync_securities()

    assert str(raised.value) == "market data synchronization failed"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 599])
@pytest.mark.asyncio
async def test_sync_securities_classifies_transient_http_statuses(status_code: int) -> None:
    client = HttpMarketDataClient(
        "http://market-data-service:8001",
        client=RecordingHttpClient(FakeResponse({}, status_code=status_code)),
    )

    with pytest.raises(RetryableMarketDataClientError):
        await client.sync_securities()


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
@pytest.mark.asyncio
async def test_sync_securities_classifies_permanent_http_statuses(status_code: int) -> None:
    client = HttpMarketDataClient(
        "http://market-data-service:8001",
        client=RecordingHttpClient(FakeResponse({}, status_code=status_code)),
    )

    with pytest.raises(PermanentMarketDataClientError):
        await client.sync_securities()


@pytest.mark.asyncio
async def test_sync_securities_classifies_network_failures_as_retryable() -> None:
    client = HttpMarketDataClient(
        "http://market-data-service:8001",
        client=RaisingHttpClient(ConnectionError("token=do-not-print")),
    )

    with pytest.raises(RetryableMarketDataClientError):
        await client.sync_securities()


@pytest.mark.asyncio
async def test_sync_securities_does_not_wrap_unexpected_transport_errors() -> None:
    client = HttpMarketDataClient(
        "http://market-data-service:8001",
        client=RaisingHttpClient(RuntimeError("programming bug")),
    )

    with pytest.raises(RuntimeError, match="programming bug"):
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
            RetryableMarketDataClientError("provider-token=do-not-print"),
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
    "outcome",
    [
        PermanentMarketDataClientError("provider-token=do-not-print"),
        {"succeeded": "2", "failed": 0},
        {"succeeded": 1, "failed": 1},
    ],
)
def test_bootstrap_securities_does_not_retry_permanent_failures(outcome, capsys) -> None:
    client = ScriptedMarketDataClient([outcome])
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
    assert exit_code == 1
    assert client.sync_calls == 1
    assert delays == []
    assert client.closed is True
    assert "do-not-print" not in captured.out + captured.err


def test_bootstrap_securities_does_not_retry_unexpected_exceptions(capsys) -> None:
    client = ScriptedMarketDataClient([RuntimeError("programming bug")])
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    exit_code = main(
        ["bootstrap-securities", "--retry-until-success"],
        settings=cli_settings(),
        market_data_client_factory=ClientFactory(client),
        sleep=record_sleep,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert client.sync_calls == 1
    assert delays == []
    assert client.closed is True
    assert "programming bug" not in captured.out + captured.err


def test_bootstrap_securities_propagates_cancellation_after_close() -> None:
    client = ScriptedMarketDataClient([asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        main(
            ["bootstrap-securities", "--retry-until-success"],
            settings=cli_settings(),
            market_data_client_factory=ClientFactory(client),
        )

    assert client.sync_calls == 1
    assert client.closed is True


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
