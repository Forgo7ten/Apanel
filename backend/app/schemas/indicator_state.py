"""Public projections for persisted indicators and states."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class IndicatorSnapshotData(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    trade_date: date
    indicator_type: str
    # Stable identity of the canonical parameter variant.  History consumers
    # must never infer identity from indicator_type alone because one security
    # can contain RSI6/RSI14 or multiple BOLL configurations on the same day.
    parameter_key: str
    parameters: dict[str, Any]
    values: dict[str, float]
    previous_values: dict[str, float] | None = None
    delta: dict[str, float] | None = None


class IndicatorHistoryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IndicatorSnapshotData]


class StateData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_id: str
    state_code: str
    title: str
    level: str
    indicator_type: str
    status: str
    active: bool
    transition: bool
    trade_date: date
    metadata: dict[str, Any]


class StateHistoryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StateData]


__all__ = [
    "IndicatorHistoryData",
    "IndicatorSnapshotData",
    "StateData",
    "StateHistoryData",
]
