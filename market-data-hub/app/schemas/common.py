"""Shared API response schemas."""

from pydantic import BaseModel, ConfigDict


class ErrorResponse(BaseModel):
    """Stable error envelope used by API endpoints."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
