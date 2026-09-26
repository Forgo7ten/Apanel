import asyncio
import threading
import time
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.market_data import Adjustment, InvalidMarketDataError, Market, SecurityType
from app.providers.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.tdx import TDXProvider
from app.providers.tdx_client import PytdxClient


class FakeTdxClient:
    def fetch_symbols(self):
        return [{"code": "600519", "name": "贵州茅台", "market": "SH", "security_type": "STOCK"}]

    def fetch_quote(self, symbol):
        return {
            "code": symbol,
            "price": "1680.20",
            "change": "1.20",
            "change_percent": "0.07",
            "timestamp": datetime(2026, 9, 23, 2, 30, tzinfo=UTC),
        }

    def fetch_daily_bars(self, symbol, start, end, adjustment):
        return [
            {
                "code": symbol,
                "date": "2026-09-23",
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10.5",
                "volume": "1000",
                "amount": "10500",
            }
        ]

    def fetch_dividends(self, symbol):
        return [{"code": symbol, "date": "2026-06-30", "cash_amount": "2.00"}]


def test_tdx_adapter_maps_injected_protocol_client_without_network() -> None:
    provider = TDXProvider(FakeTdxClient(), timeout_seconds=1)

    symbols = asyncio.run(provider.get_symbols())
    quote = asyncio.run(provider.get_quote("SH.600519"))
    bars = asyncio.run(
        provider.get_daily_bars(
            "600519",
            date(2026, 1, 1),
            date(2026, 9, 23),
            adjustment=Adjustment.QFQ,
        )
    )
    dividends = asyncio.run(provider.get_dividends("600519"))

    assert symbols[0].market is Market.SH
    assert symbols[0].security_type is SecurityType.STOCK
    assert quote.price == Decimal("1680.20")
    assert bars[0].adjustment is Adjustment.QFQ
    assert dividends[0].cash_amount == Decimal("2.00")


def test_tdx_adapter_runs_blocking_client_in_thread_and_maps_timeout() -> None:
    class SlowClient(FakeTdxClient):
        def fetch_quote(self, symbol):
            time.sleep(0.05)
            return super().fetch_quote(symbol)

    async def exercise() -> None:
        provider = TDXProvider(SlowClient(), timeout_seconds=0.001)
        started = time.perf_counter()
        with pytest.raises(ProviderTimeoutError, match="TDX quote"):
            await provider.get_quote("600519")
        assert time.perf_counter() - started < 0.04

    asyncio.run(exercise())


def test_tdx_adapter_filters_only_supported_stock_and_etf_records() -> None:
    class SymbolClient(FakeTdxClient):
        def fetch_symbols(self):
            return [
                {"code": "000001", "name": "平安银行"},
                {"code": "159915", "name": "创业板ETF"},
                {"code": "600519", "name": "贵州茅台", "type": "STOCK"},
                {"code": "510300", "name": "沪深300ETF", "type": "ETF"},
                {"code": "830001", "name": "北证样本", "type": "STOCK"},
                {"code": "920001", "name": "北证新样本", "type": "STOCK"},
                {"code": "000300", "name": "沪深300指数", "type": "INDEX"},
                {"code": "150001", "name": "基金份额", "type": "FUND"},
                {"code": "200001", "name": "深证B股", "type": "B_SHARE"},
                {"code": "999999", "name": "未知证券"},
            ]

    provider = TDXProvider(SymbolClient(), timeout_seconds=1)

    symbols = asyncio.run(provider.get_symbols())

    assert [(item.symbol, item.market, item.security_type) for item in symbols] == [
        ("000001", Market.SZ, SecurityType.STOCK),
        ("159915", Market.SZ, SecurityType.ETF),
        ("600519", Market.SH, SecurityType.STOCK),
        ("510300", Market.SH, SecurityType.ETF),
        ("830001", Market.BJ, SecurityType.STOCK),
        ("920001", Market.BJ, SecurityType.STOCK),
    ]


def test_tdx_adapter_filters_unknown_bond_and_index_on_the_production_path() -> None:
    class SymbolListClient:
        def connect(self, host, port, *, time_out):
            return self

        def close(self):
            return None

        def get_security_count(self, market):
            return {0: 5, 1: 0}[market]

        def get_security_list(self, market, offset):
            if market == 0 and offset == 0:
                return [
                    {"code": "110001", "name": "国债", "type": "BOND"},
                    {"code": "000300", "name": "沪深300指数", "type": "INDEX"},
                    {"code": "999999", "name": "未知证券"},
                    {"code": "999998"},
                    {"code": "600519", "name": "贵州茅台", "type": "STOCK"},
                ]
            return []

    provider = TDXProvider(
        PytdxClient(
            ("good.test:7709",),
            retry_attempts=0,
            client_factory=SymbolListClient,
        ),
        timeout_seconds=1,
    )

    symbols = asyncio.run(provider.get_symbols())

    assert [(item.symbol, item.market, item.security_type) for item in symbols] == [
        ("600519", Market.SH, SecurityType.STOCK),
    ]


def test_tdx_adapter_rejects_security_type_conflict_instead_of_defaulting() -> None:
    class ConflictingClient(FakeTdxClient):
        def fetch_symbols(self):
            return [{"code": "600519", "name": "贵州茅台", "type": "ETF"}]

    with pytest.raises(InvalidMarketDataError, match="conflict"):
        asyncio.run(TDXProvider(ConflictingClient()).get_symbols())


def test_tdx_adapter_uses_an_independent_symbol_timeout() -> None:
    class SlowSymbols(FakeTdxClient):
        def fetch_symbols(self):
            time.sleep(0.05)
            return super().fetch_symbols()

    async def exercise() -> None:
        provider = TDXProvider(
            SlowSymbols(),
            timeout_seconds=1,
            symbol_timeout_seconds=0.001,
        )
        with pytest.raises(ProviderTimeoutError, match="TDX symbols"):
            await provider.get_symbols()

    asyncio.run(exercise())


def test_symbol_timeout_does_not_start_an_overlapping_low_level_request() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    factory_calls = 0

    class BlockingSymbolsClient:
        def connect(self, host, port, *, time_out):
            return self

        def close(self):
            finished.set()

        def get_security_count(self, market):
            if market == 0:
                started.set()
                assert release.wait(1)
                return 1
            return 0

        def get_security_list(self, market, offset):
            return [{"code": "600519", "name": "贵州茅台"}]

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return BlockingSymbolsClient()

    async def exercise() -> None:
        provider = TDXProvider(
            PytdxClient(
                ("good.test:7709",),
                retry_attempts=0,
                client_factory=factory,
            ),
            timeout_seconds=1,
            symbol_timeout_seconds=0.001,
        )
        first = asyncio.create_task(provider.get_symbols())
        assert await asyncio.to_thread(started.wait, 1)
        with pytest.raises(ProviderTimeoutError, match="TDX symbols"):
            await first

        with pytest.raises(ProviderUnavailableError, match="already in progress"):
            await provider.get_symbols()
        assert factory_calls == 1

        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        symbols = await provider.get_symbols()
        assert [item.symbol for item in symbols] == ["600519"]
        assert factory_calls == 2

    asyncio.run(exercise())
