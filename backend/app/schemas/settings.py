"""User settings API projections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserSettingsUpdateRequest(BaseModel):
    """Partial settings payload; unknown keys are intentionally preserved."""

    model_config = ConfigDict(extra="allow")

    settings: dict[str, Any] | None = None


class UserSettingsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = ["UserSettingsData", "UserSettingsUpdateRequest"]
