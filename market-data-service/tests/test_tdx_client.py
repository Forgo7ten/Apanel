import asyncio
from datetime import date
from decimal import Decimal

import pytest

from app.domain.market_data import Adjustment
from app.providers.errors import (
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUnsupportedError,
)
from app.providers.tdx_client import PytdxClient, TDXServer


class FakeLowLevelClient:
    def __init__(self, *, connect_error: BaseException | None = None) -> None:
        self.connect_error = connect_error
        self.connect_calls: list[tuple[str, int, float]] = []
        self.close_calls = 0
        self.quote_calls: list[list[tuple[int, str]]] = []
        self.bar_calls: list[tuple[int, int, str, int, int]] = []
        self.security_count_calls: list[int] = []
        self.security_list_calls: list[tuple[int, int]] = []

    def connect(self, host: str, port: int, *, time_out: float):
        self.connect_calls.append((host, port, time_out))
        if self.connect_error is not None:
            raise self.connect_error
        return self

    def close(self) -> None:
        self.close_calls += 1

    def get_security_count(self, market: int) -> int:
        self.security_count_calls.append(market)
        return {0: 3, 1: 1}[market]

    def get_security_list(self, market: int, start: int):
        self.security_list_calls.append((market, start))
        pages = {
            (0, 0): [
                {"code": "000001", "name": "平安银行"},
                {"code": "830001", "name": "北证样本"},
            ],
            (0, 2): [{"code": "159915", "name": "创业板ETF"}],
            (1, 0): [{"code": "600519", "name": "贵州茅台"}],
        }
        return pages.get((market, start), [])

    def get_security_quotes(self, symbols):
        self.quote_calls.append(symbols)
        return [
            {
                "market": symbols[0][0],
                "code": symbols[0][1],
                "price": 10.5,
                "last_close": 10,
                "servertime": "2026-09-24 10:30:00",
            }
        ]

    def get_security_bars(self, category, market, code, start, count):
        self.bar_calls.append((category, market, code, start, count))
        pages = {
            0: [
                {
                    "code": code,
                    "datetime": "2026-01-01 15:00:00",
                    "open": 9,
                    "high": 11,
                    "low": 8,
                    "close": 10,
                    "vol": 10,
                },
                {
                    "code": code,
                    "datetime": "2026-01-02 15:00:00",
                    "open": 10,
                    "high": 12,
                    "low": 9,
                    "close": 11,
                    "vol": 20,
                },
            ],
            2: [
                {
                    "code": code,
                    "datetime": "2026-01-03 15:00:00",
                    "open": 11,
                    "high": 13,
                    "low": 10,
                    "close": 12,
                    "vol": 30,
                },
            ],
        }
        return pages.get(start, [])


def _factory_for(*clients):
    queue = list(clients)

    def factory():
        return queue.pop(0)

    return factory


def test_tdx_server_parses_host_port_and_ipv6() -> None:
    assert TDXServer.parse("example.test:7709") == TDXServer("example.test", 7709)
    assert TDXServer.parse("[::1]:7709") == TDXServer("::1", 7709)
    assert TDXServer.parse(("example.test", 7710)) == TDXServer("example.test", 7710)


def test_client_fails_over_and_closes_failed_and_successful_connections() -> None:
    failed = FakeLowLevelClient(connect_error=TimeoutError("first host timed out"))
    healthy = FakeLowLevelClient()
    client = PytdxClient(
        ("bad.test:7709", "good.test:7709"),
        timeout_seconds=0.25,
        retry_attempts=0,
        client_factory=_factory_for(failed, healthy),
    )

    quote = client.fetch_quote("SH.600519")

    assert quote["symbol"] == "600519"
    assert quote["change"] == Decimal("0.5")
    assert failed.close_calls == 1
    assert healthy.close_calls == 1
    assert healthy.quote_calls == [[(1, "600519")]]


def test_client_reports_timeout_after_all_retry_rounds() -> None:
    clients = [FakeLowLevelClient(connect_error=TimeoutError("down")) for _ in range(4)]
    client = PytdxClient(
        ("one.test:7709", "two.test:7709"),
        retry_attempts=1,
        client_factory=_factory_for(*clients),
    )

    with pytest.raises(ProviderTimeoutError, match="quote"):
        client.fetch_quote("000001")
    assert all(item.close_calls == 1 for item in clients)


def test_client_scans_only_standard_lists_and_preserves_raw_records() -> None:
    low_level = FakeLowLevelClient()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    records = client.fetch_symbols()

    assert [record["code"] for record in records] == [
        "000001",
        "830001",
        "159915",
        "600519",
    ]
    assert all("market" not in record for record in records)
    assert low_level.security_count_calls == [0, 1]
    assert all(market in {0, 1} for market, _ in low_level.security_list_calls)


def test_client_fails_closed_when_counted_list_ends_before_count() -> None:
    class EarlyEmpty(FakeLowLevelClient):
        def get_security_count(self, market: int) -> int:
            self.security_count_calls.append(market)
            return {0: 2, 1: 1}[market]

        def get_security_list(self, market: int, start: int):
            self.security_list_calls.append((market, start))
            if market == 0 and start == 0:
                return [{"code": "000001", "name": "平安银行"}]
            if market == 1 and start == 0:
                return [{"code": "600519", "name": "贵州茅台"}]
            return []

    low_level = EarlyEmpty()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    with pytest.raises(ProviderUnavailableError, match="symbols"):
        client.fetch_symbols()


def test_bj_single_security_queries_prefer_market_zero_and_bound_fallback_to_two() -> None:
    class RoutingClient(FakeLowLevelClient):
        def __init__(self) -> None:
            super().__init__()
            self.xdxr_calls: list[tuple[int, str]] = []

        def get_security_quotes(self, symbols):
            self.quote_calls.append(symbols)
            if symbols[0][0] == 0:
                return []
            return [{"code": symbols[0][1], "price": 10, "last_close": 9}]

        def get_security_bars(self, category, market, code, start, count):
            self.bar_calls.append((category, market, code, start, count))
            if market == 0:
                return []
            return [
                {
                    "code": code,
                    "datetime": "2026-01-01 15:00:00",
                    "open": 9,
                    "high": 11,
                    "low": 8,
                    "close": 10,
                    "vol": 10,
                }
            ]

        def get_xdxr_info(self, market, code):
            self.xdxr_calls.append((market, code))
            if market == 0:
                return []
            return [{"year": 2026, "month": 1, "day": 1, "fenhong": "1"}]

    low_level = RoutingClient()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    quote = client.fetch_quote("BJ.830001")
    bars = client.fetch_daily_bars(
        "BJ.830001", date(2026, 1, 1), date(2026, 1, 1), Adjustment.NONE
    )
    dividends = client.fetch_dividends("BJ.830001")

    assert quote["symbol"] == "830001"
    assert bars and bars[0]["symbol"] == "830001"
    assert dividends[0]["cash_amount"] == "1"
    assert [call[0][0] for call in low_level.quote_calls] == [0, 2]
    assert [call[1] for call in low_level.bar_calls] == [0, 2]
    assert [market for market, _ in low_level.xdxr_calls] == [0, 2]


@pytest.mark.parametrize("action_response", [None, []])
def test_bj_qfq_accepts_empty_corporate_actions_after_bounded_fallback(action_response) -> None:
    class NoBjCorporateActions(FakeLowLevelClient):
        def __init__(self) -> None:
            super().__init__()
            self.xdxr_calls: list[tuple[int, str]] = []

        def get_xdxr_info(self, market, code):
            self.xdxr_calls.append((market, code))
            return action_response

        def get_security_bars(self, category, market, code, start, count):
            self.bar_calls.append((category, market, code, start, count))
            return [
                {
                    "code": code,
                    "datetime": "2026-01-01 15:00:00",
                    "open": 9,
                    "high": 11,
                    "low": 8,
                    "close": 10,
                    "vol": 10,
                }
            ]

    low_level = NoBjCorporateActions()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    records = client.fetch_daily_bars(
        "BJ.830001", date(2026, 1, 1), date(2026, 1, 1), Adjustment.QFQ
    )

    assert records and records[0]["adjustment"] == "qfq"
    assert [market for market, _ in low_level.xdxr_calls] == [0, 2]


def test_attribute_error_inside_bj_method_does_not_trigger_market_fallback() -> None:
    class BrokenQuoteClient(FakeLowLevelClient):
        def get_security_quotes(self, symbols):
            self.quote_calls.append(symbols)
            raise AttributeError("bug inside get_security_quotes")

    low_level = BrokenQuoteClient()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    with pytest.raises(ProviderUnavailableError, match="quote"):
        client.fetch_quote("BJ.830001")

    assert [call[0][0] for call in low_level.quote_calls] == [0]


def test_missing_bj_quote_method_is_classified_before_querying() -> None:
    class NoQuoteMethod:
        def connect(self, host, port, *, time_out):
            return self

        def close(self):
            return None

    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=NoQuoteMethod,
    )

    with pytest.raises(ProviderUnsupportedError, match="get_security_quotes"):
        client.fetch_quote("BJ.830001")


def test_client_pages_filters_sorts_deduplicates_and_marks_raw_bars_none() -> None:
    low_level = FakeLowLevelClient()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        bar_page_size=2,
        client_factory=lambda: low_level,
    )

    records = client.fetch_daily_bars(
        "000001",
        date(2026, 1, 2),
        date(2026, 1, 3),
        Adjustment.NONE,
    )

    assert [record["trade_date"] for record in records] == [
        date(2026, 1, 2),
        date(2026, 1, 3),
    ]
    assert all(record["adjustment"] == "none" for record in records)
    assert [call[3] for call in low_level.bar_calls] == [0, 2]


def test_qfq_requires_explicit_factor_transformer_and_never_labels_raw_data() -> None:
    low_level = FakeLowLevelClient()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    with pytest.raises(ProviderUnsupportedError, match="unadjusted"):
        client.fetch_daily_bars(
            "000001", date(2026, 1, 1), date(2026, 1, 2), Adjustment.QFQ
        )
    assert not low_level.connect_calls

    adjusted = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
        qfq_transformer=lambda symbol, records: [
            {**record, "close": 10.0, "adjustment": "qfq"} for record in records
        ],
    )
    records = adjusted.fetch_daily_bars(
        "000001", date(2026, 1, 1), date(2026, 1, 2), Adjustment.QFQ
    )
    assert records
    assert all(record["adjustment"] == "qfq" for record in records)


def test_qfq_is_available_by_default_when_tdx_exposes_corporate_actions() -> None:
    class WithCorporateActions(FakeLowLevelClient):
        def get_xdxr_info(self, market, code):
            return [{"year": 2026, "month": 1, "day": 2, "fenhong": "1", "pre_close": "10"}]

    low_level = WithCorporateActions()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    qfq = client.fetch_daily_bars("000001", date(2026, 1, 1), date(2026, 1, 2))
    none = client.fetch_daily_bars(
        "000001", date(2026, 1, 1), date(2026, 1, 2), Adjustment.NONE
    )

    assert [record["close"] for record in qfq] == [Decimal("9.0"), Decimal("11")]
    assert [record["close"] for record in none] == [10, 11]
    assert all(record["adjustment"] == "qfq" for record in qfq)
    assert all(record["adjustment"] == "none" for record in none)


@pytest.mark.parametrize("action_response", [None, []])
def test_qfq_accepts_a_security_without_corporate_actions(action_response) -> None:
    class NoCorporateActions(FakeLowLevelClient):
        def get_xdxr_info(self, market, code):
            return action_response

    low_level = NoCorporateActions()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    records = client.fetch_daily_bars(
        "600519", date(2026, 1, 1), date(2026, 1, 2), Adjustment.QFQ
    )
    dividends = client.fetch_dividends("600519")

    assert [record["close"] for record in records] == [10, 11]
    assert all(record["adjustment"] == "qfq" for record in records)
    assert not dividends


def test_client_wraps_low_level_response_errors_and_closes_connection() -> None:
    class BrokenBars(FakeLowLevelClient):
        def get_security_bars(self, category, market, code, start, count):
            return None

    low_level = BrokenBars()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    with pytest.raises(ProviderUnavailableError, match="daily bars"):
        client.fetch_daily_bars("000001", date(2026, 1, 1), date(2026, 1, 2), Adjustment.NONE)
    assert low_level.close_calls == 1


def test_provider_runs_real_client_blocking_calls_off_event_loop() -> None:
    from app.providers.tdx import TDXProvider

    low_level = FakeLowLevelClient()
    provider = TDXProvider(
        PytdxClient(("good.test:7709",), retry_attempts=0, client_factory=lambda: low_level),
        timeout_seconds=1,
    )
    quote = asyncio.run(provider.get_quote("BJ.830001"))

    assert quote.symbol == "830001"
    assert quote.timestamp.isoformat() == "2026-09-24T02:30:00+00:00"
