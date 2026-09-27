"""Typed user settings API projections with forward-compatible extras."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IndicatorSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    defaults: list[str] | None = None
    parameters: dict[str, dict[str, Any]] | None = None


class DisplaySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    density: Literal["compact", "comfortable"] | None = None
    show_states: bool | None = None
    show_deltas: bool | None = None
    show_mini_chart: bool | None = None


class NotificationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Omitted = keep existing secret; explicit null = clear.
    feishu_webhook: str | None = None


class UserSettingsUpdateRequest(BaseModel):
    """Partial settings payload with typed v1 keys and forward-compatible extras.

    ``settings`` remains available for legacy clients that wrapped the payload
    in one object.  New clients should send the typed top-level fields.
    """

    model_config = ConfigDict(extra="allow")

    settings: dict[str, Any] | None = None
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
