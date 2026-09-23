"""Health endpoint response contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import ErrorResponse


class DependencyHealth(BaseModel):
    """Status and safe diagnostic data for one infrastructure dependency."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "unhealthy"]
    latency_ms: float | None = Field(default=None, ge=0)
    detail: str | None = None


class HealthData(BaseModel):
    """Service health snapshot returned in both healthy and degraded cases."""

    model_config = ConfigDict(extra="forbid")

    service: str
    version: str
    status: Literal["healthy", "degraded"]
    dependencies: dict[str, DependencyHealth]


class HealthResponse(BaseModel):
    """Common success envelope for the backend health endpoint."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: HealthData
    error: ErrorResponse | None = None
