"""Wire schemas for the market-data service's internal API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.domain.market_data import Adjustment

from .common import ErrorResponse


class ApiEnvelope(BaseModel):
    """Common success/error envelope shared by all internal endpoints."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any | None = None
    error: ErrorResponse | None = None


class QuoteData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    price: Decimal
    change: Decimal | None = None
    change_percent: Decimal | None = None
    timestamp: datetime


class DailyBarData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None = None
    adjustment: Adjustment


class DailyBarsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    start: date | None = None
    end: date | None = None
    adjustment: Adjustment
    items: list[DailyBarData]


class DailySyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    symbols: list[str] = Field(min_length=1)
    start: date = Field(validation_alias=AliasChoices("start", "start_date"))
    end: date = Field(validation_alias=AliasChoices("end", "end_date"))
    adjustment: Adjustment = Field(
        default=Adjustment.QFQ,
        validation_alias=AliasChoices("adjustment", "adjust", "adjust_type"),
    )


class SyncItemData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    status: str
    fetched: int
    persisted: int
    error: ErrorResponse | None = None


class SyncSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    total: int
    succeeded: int
    failed: int
    ok: bool
    items: list[SyncItemData]
