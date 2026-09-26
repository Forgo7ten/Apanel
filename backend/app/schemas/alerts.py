"""Request and response contracts for alert rules and notification history."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.schemas.security import SecurityData


def _validate_threshold(value: float | None) -> float | None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
        raise ValueError("threshold must be a finite number")
    return value


class AlertRuleCreateRequest(BaseModel):
    """Flat payload used by the notification workspace wizard."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    security_id: int = Field(gt=0)
    condition_type: Literal["VALUE", "STATE"]
    state_id: str | None = Field(default=None, min_length=1, max_length=64)
    state_code: str | None = Field(default=None, min_length=1, max_length=64)
    indicator: str | None = Field(
        default=None,
        validation_alias=AliasChoices("indicator", "indicator_type"),
        min_length=1,
        max_length=32,
    )
    operator: str | None = Field(default=None, min_length=1, max_length=4)
    threshold: float | None = None
    enabled: bool = True

    @field_validator("condition_type", mode="before")
    @classmethod
    def normalize_condition_type(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("state_id", "state_code", "indicator", "operator")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator("threshold")
    @classmethod
    def finite_threshold(cls, value: float | None) -> float | None:
        return _validate_threshold(value)


class AlertRuleUpdateRequest(BaseModel):
    """Partial rule update; omitted fields retain their current value."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    security_id: int | None = Field(default=None, gt=0)
    condition_type: Literal["VALUE", "STATE"] | None = None
    state_id: str | None = Field(default=None, min_length=1, max_length=64)
    state_code: str | None = Field(default=None, min_length=1, max_length=64)
    indicator: str | None = Field(
        default=None,
        validation_alias=AliasChoices("indicator", "indicator_type"),
        min_length=1,
        max_length=32,
    )
    operator: str | None = Field(default=None, min_length=1, max_length=4)
    threshold: float | None = None
    enabled: bool | None = None

    @field_validator("condition_type", mode="before")
    @classmethod
    def normalize_condition_type(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("state_id", "state_code", "indicator", "operator")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator("threshold")
    @classmethod
    def finite_threshold(cls, value: float | None) -> float | None:
        return _validate_threshold(value)


class AlertRuleData(BaseModel):
    """Frontend-compatible projection of one rule."""

    model_config = ConfigDict(extra="forbid")

    id: int
    security_id: int
    condition_type: str
    state_id: str | None = None
    state_code: str | None = None
    indicator: str | None = None
    indicator_type: str | None = None
    operator: str | None = None
    threshold: float | None = None
    enabled: bool
    symbol: str | None = None
    name: str | None = None
    security: SecurityData | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationData(BaseModel):
    """Frontend-compatible notification history projection."""

    model_config = ConfigDict(extra="forbid")

    id: int
    alert_rule_id: int | None = None
    security_id: int | None = None
    symbol: str
    name: str | None = None
    title: str
    channel: str
    status: str
    content: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    indicator: str | None = None
    state_id: str | None = None
    created_at: datetime
    sent_at: datetime | None = None


__all__ = [
    "AlertRuleCreateRequest",
    "AlertRuleData",
    "AlertRuleUpdateRequest",
    "NotificationData",
]
