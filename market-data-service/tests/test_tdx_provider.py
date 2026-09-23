import asyncio
import time
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.market_data import Adjustment, Market, SecurityType
from app.providers.errors import ProviderTimeoutError
from app.providers.tdx import TDXProvider


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
