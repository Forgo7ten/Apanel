"""Request and response models for the user watch-table workspace."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.schemas.security import SecurityData


class WatchTableCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class WatchTableSummaryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    description: str | None = None
    stock_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AddStockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_id: int = Field(validation_alias=AliasChoices("security_id", "id"), gt=0)


class StockPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_id: int = Field(gt=0)
    position: int = Field(ge=0)


class StockReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StockPositionRequest] = Field(min_length=1)


class ColumnCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    column_type: str = Field(
        default="INDICATOR",
        validation_alias=AliasChoices("column_type", "type"),
    )
    indicator_type: str | None = Field(default=None, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)
    view_mode: str = Field(default="NUMBER", max_length=16)
    position: int | None = Field(default=None, ge=0)
    visible: bool = True
    width: int | None = Field(default=None, ge=1, le=2000)

    @field_validator("column_type", "view_mode", "indicator_type")
    @classmethod
    def strip_values(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else value


class ColumnUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible: bool | None = None
    hidden: bool | None = None
    position: int | None = Field(default=None, ge=0)
    order: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1, le=2000)
    view_mode: str | None = Field(default=None, max_length=16)
    parameters: dict[str, Any] | None = None

    @field_validator("view_mode")
    @classmethod
    def strip_view_mode(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else value


class ColumnReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_ids: list[int] = Field(min_length=1)


class TableColumnData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    column_type: str
    type: str
    indicator_type: str | None = None
    parameters: dict[str, Any]
    view_mode: str
    position: int
    order: int
    visible: bool
    hidden: bool
    width: int | None = None


class PriceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    price: float | None = None
    change: float | None = None
    delta: float | None = None
    change_percent: float | None = None
    direction: str | None = None
    timestamp: datetime | None = None


class CurrentStateData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_id: str
    state_code: str
    title: str
    level: str
    indicator_type: str
    status: str
    active: bool
    transition: bool = False
    trade_date: date
    metadata: dict[str, Any] = Field(default_factory=dict)


class WatchTableStockData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_id: int
    symbol: str
    name: str
    market: str
    security: SecurityData
    price: PriceData | None = None
    indicators: dict[str, dict[str, Any]] = Field(default_factory=dict)
    indicator_values: dict[str, dict[str, Any]] = Field(default_factory=dict)
    values: dict[str, dict[str, Any]] = Field(default_factory=dict)
    states: list[CurrentStateData] = Field(default_factory=list)


class WatchTableDetailsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    description: str | None = None
    stock_count: int
    columns: list[TableColumnData]
    stocks: list[WatchTableStockData]


class DeleteData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool = True


__all__ = [
    "AddStockRequest",
    "ColumnCreateRequest",
    "ColumnReorderRequest",
    "ColumnUpdateRequest",
    "CurrentStateData",
    "DeleteData",
    "PriceData",
    "StockPositionRequest",
    "StockReorderRequest",
    "TableColumnData",
    "WatchTableCreateRequest",
    "WatchTableDetailsData",
    "WatchTableStockData",
    "WatchTableSummaryData",
]
