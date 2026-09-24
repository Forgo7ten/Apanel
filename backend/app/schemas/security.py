"""Public security and shared market-data projections."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


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


__all__ = ["DailyBarData", "DailyBarsData", "QuoteData", "SecurityData"]
