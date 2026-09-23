from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.market_data import (
    Adjustment,
    DailyBar,
    Dividend,
    InvalidMarketDataError,
    Market,
    Quote,
    Security,
    SecurityType,
    UnknownAdjustmentError,
    UnknownMarketError,
    normalize_adjustment,
    normalize_symbol,
)


def test_security_normalizes_supported_a_share_and_etf_symbols() -> None:
    security = Security(
        symbol="sh.600519",
        name="贵州茅台",
        market="SH",
        security_type="STOCK",
    )
    etf = Security(
        symbol="SZ159915",
        name="创业板ETF",
        market=Market.SZ,
        security_type=SecurityType.ETF,
    )

    assert security.symbol == "600519"
    assert security.market is Market.SH
    assert security.security_type is SecurityType.STOCK
    assert etf.symbol == "159915"
    assert etf.market is Market.SZ


def test_symbol_and_enum_inputs_are_rejected_without_silent_fallback() -> None:
    with pytest.raises(UnknownMarketError):
        normalize_symbol("XX.600519")

    with pytest.raises(UnknownMarketError):
        Security(symbol="600519", name="bad", market="XX", security_type="STOCK")

    with pytest.raises(UnknownAdjustmentError):
        normalize_adjustment("hfq")


def test_daily_bar_rejects_invalid_ohlc_and_negative_volume() -> None:
    common = {
        "symbol": "600519",
        "trade_date": date(2026, 9, 23),
        "open": Decimal("10"),
        "high": Decimal("9"),
        "low": Decimal("8"),
        "close": Decimal("8.5"),
        "volume": Decimal("100"),
        "adjustment": Adjustment.NONE,
    }
    with pytest.raises(InvalidMarketDataError, match="high"):
        DailyBar(**common)

    common["high"] = Decimal("11")
    common["volume"] = Decimal("-1")
    with pytest.raises(InvalidMarketDataError, match="volume"):
        DailyBar(**common)


def test_decimal_and_timezone_semantics_are_explicit() -> None:
    quote = Quote(
        symbol="SH.600519",
        price="1680.20",
        change="-2.10",
        change_percent="-0.12",
        timestamp=datetime(2026, 9, 23, 2, 30, tzinfo=UTC),
    )
    dividend = Dividend(
        symbol="600519",
        date=date(2026, 6, 30),
        cash_amount="2.00",
    )

    assert quote.price == Decimal("1680.20")
    assert quote.timestamp.tzinfo is not None
    assert quote.timestamp.utcoffset() == UTC.utcoffset(quote.timestamp)
    assert dividend.cash_amount == Decimal("2.00")

    with pytest.raises(InvalidMarketDataError, match="timezone"):
        Quote(
            symbol="600519",
            price=Decimal("1"),
            timestamp=datetime(2026, 9, 23, 2, 30),
        )
