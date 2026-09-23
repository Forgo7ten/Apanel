"""Shared API response schemas."""

from pydantic import BaseModel, ConfigDict


class ErrorResponse(BaseModel):
    """Stable error envelope used by API endpoints."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class SuccessResponse[T](BaseModel):
    """Stable success envelope used by JSON API endpoints."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    data: T


class FailureResponse(BaseModel):
    """Stable failure envelope used by JSON API endpoints."""

    model_config = ConfigDict(extra="forbid")

    success: bool = False
    error: ErrorResponse
