import asyncio
from datetime import date
from decimal import Decimal

import pytest

from app.domain.market_data import Adjustment, Market
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

    def connect(self, host: str, port: int, *, time_out: float):
        self.connect_calls.append((host, port, time_out))
        if self.connect_error is not None:
            raise self.connect_error
        return self

    def close(self) -> None:
        self.close_calls += 1

    def get_security_count(self, market: int) -> int:
        return {0: 2, 1: 1, 2: 1}[market]

    def get_security_list(self, market: int, start: int):
        pages = {
            (0, 0): [{"code": "000001", "name": "平安银行"}],
            (0, 1): [{"code": "159915", "name": "创业板ETF"}],
            (1, 0): [{"code": "600519", "name": "贵州茅台"}],
            (2, 0): [{"code": "830001", "name": "北证样本"}],
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


def test_client_maps_all_supported_markets_and_pages_symbols() -> None:
    low_level = FakeLowLevelClient()
    client = PytdxClient(
        ("good.test:7709",),
        retry_attempts=0,
        client_factory=lambda: low_level,
    )

    records = client.fetch_symbols()

    assert [(record["code"], record["market"]) for record in records] == [
        ("000001", Market.SZ),
        ("159915", Market.SZ),
        ("600519", Market.SH),
        ("830001", Market.BJ),
    ]


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
