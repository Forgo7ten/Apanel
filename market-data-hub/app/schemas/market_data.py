"""Wire schemas for the Market Data Hub's internal API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

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


class DividendData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    date: date
    cash_amount: Decimal


class DividendsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    start: date | None = None
    end: date | None = None
    items: list[DividendData]


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

    symbols: list[str] = Field(min_length=1, max_length=100)
    start: date | None = Field(default=None, validation_alias=AliasChoices("start", "start_date"))
    end: date | None = Field(default=None, validation_alias=AliasChoices("end", "end_date"))
    lookback_bars: int | None = Field(default=None, gt=0, le=10000)
    adjustment: Adjustment = Field(
        default=Adjustment.QFQ,
        validation_alias=AliasChoices("adjustment", "adjust", "adjust_type"),
    )

    @model_validator(mode="after")
    def validate_mode(self) -> DailySyncRequest:
        if self.lookback_bars is not None:
            if self.start is not None or self.end is not None:
                raise ValueError("lookback_bars is exclusive with start/end")
        elif self.start is None or self.end is None or self.start > self.end:
            raise ValueError("start/end are required and must form a valid range")
        return self


class QuoteSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=100)


class DividendSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1, max_length=100)


class SyncItemData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    status: str
    fetched: int
    persisted: int
    error: ErrorResponse | None = None
    history_rebased: bool = False
    changed_from: date | None = None


class SyncSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str
    total: int
    succeeded: int
    failed: int
    ok: bool
    items: list[SyncItemData]


class CalendarValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date


class CalendarValidationData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date
    status: str
    expected_open: bool | None = None
    actual_open: bool | None = None
