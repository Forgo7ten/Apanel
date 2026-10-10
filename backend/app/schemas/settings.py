"""Typed user settings API projections; current fields only."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IndicatorSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: list[str] | None = None
    parameters: dict[str, dict[str, Any]] | None = None


class DisplaySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    density: Literal["compact", "comfortable"] | None = None
    show_states: bool | None = None
    show_deltas: bool | None = None
    show_mini_chart: bool | None = None


class NotificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Omitted = keep existing secret; explicit null = clear.
    feishu_webhook: str | None = None


class UserSettingsUpdateRequest(BaseModel):
    """Current typed settings request; no historic wrapper is accepted."""

    model_config = ConfigDict(extra="forbid")

    adjust_type: Literal["qfq", "none"] | None = None
    indicator_settings: IndicatorSettingsUpdate | None = None
    display_settings: DisplaySettingsUpdate | None = None
    notification_settings: NotificationSettingsUpdate | None = None


class UserSettingsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "DisplaySettingsUpdate",
    "IndicatorSettingsUpdate",
    "NotificationSettingsUpdate",
    "UserSettingsData",
    "UserSettingsUpdateRequest",
]
