"""Public security and shared market-data projections."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SecurityData(BaseModel):
    """A public security projection that always carries its database id."""

    model_config = ConfigDict(extra="forbid")

    id: int
    security_id: int
    symbol: str
    name: str
    market: str
    exchange: str
    security_type: str
    type: str
    status: str


class QuoteData(BaseModel):
    """Latest intraday quote for a security."""

    model_config = ConfigDict(extra="forbid")

    price: float
    value: float
    change: float | None = None
    change_percent: float | None = None
    timestamp: datetime


class DividendYieldData(BaseModel):
    """TTM cash-dividend yield with the inputs needed to explain it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # The application service calculates with Decimal.  Public API numbers
    # follow the existing quote/bar contract and are JSON numbers rather than
    # Decimal strings.
    dividend_total: float
    price: float
    dividend_yield: float = Field(alias="yield")
    as_of: datetime
    price_source: Literal["QUOTE", "DAILY_BAR_CLOSE"]
    window_start: date
    window_end: date
    dividend_event_count: int


class DailyBarData(BaseModel):
    """A persisted daily OHLC bar."""

    model_config = ConfigDict(extra="forbid")

    date: date
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    adjust_type: str


class DailyBarsData(BaseModel):
    """List wrapper used by the unified API response envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[DailyBarData]


__all__ = [
    "DailyBarData",
    "DailyBarsData",
    "DividendYieldData",
    "QuoteData",
    "SecurityData",
]
