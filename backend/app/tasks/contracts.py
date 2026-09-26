"""Contracts shared by scheduled jobs and the end-of-day pipeline.

The scheduler deliberately depends on these small contracts rather than on
the alert persistence implementation.  The alert work is being developed in
parallel, so importing it at module import time would make a worker impossible
to start while that branch is absent.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from app.services.indicator_service import DEFAULT_INDICATOR_ADJUSTMENT

EOD_STEP_ORDER: tuple[str, ...] = (
    "daily_sync",
    "adjustment_ready",
    "indicator_snapshots",
    "delta",
    "states",
    "alerts",
    "notifications",
)


class PipelineError(RuntimeError):
    """Base class for safe, retryable pipeline failures."""


class PipelineStepError(PipelineError):
    """A named step failed without copying a provider secret into the message."""

    def __init__(self, step: str, cause: BaseException) -> None:
        self.step = step
        self.cause = cause
        # Do not include str(cause): provider exceptions can contain URLs or
        # credentials supplied by an external service.
        super().__init__(f"scheduled pipeline step failed: {step}")


class PipelineConfigurationError(PipelineError):
    """A required pipeline integration is not configured."""


@dataclass(slots=True)
class PipelineContext:
    """Immutable-in-practice input plus private step artifacts.

    ``idempotency_key`` is stable for one trade date and adjustment mode.  All
    default persistence steps use upserts or edge-trigger state at their own
    boundaries, allowing a retry to safely execute already completed steps.
    """

    trade_date: date
    adjustment: str
    symbols: tuple[str, ...]
    idempotency_key: str | None = None
    resources: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        adjustment = str(self.adjustment).strip().lower()
        if adjustment != DEFAULT_INDICATOR_ADJUSTMENT:
            raise PipelineConfigurationError(
                "indicator and state persistence require qfq adjustment"
            )
        self.adjustment = adjustment
        self.symbols = tuple(
            dict.fromkeys(
                str(symbol).strip() for symbol in self.symbols if str(symbol).strip()
            )
        )
        if self.idempotency_key is None:
            self.idempotency_key = f"{self.trade_date.isoformat()}:{self.adjustment}"


Handler = Callable[[PipelineContext], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class PipelineStep:
    """One named, injectable pipeline step."""

    name: str
    handler: Handler

    async def run(self, context: PipelineContext) -> Any:
        result = self.handler(context)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Safe summary returned by jobs and suitable for a JSON Celery backend."""

    status: str
    trade_date: str
    adjustment: str
    symbols: int
    completed_steps: tuple[str, ...] = ()
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trade_date": self.trade_date,
            "adjustment": self.adjustment,
            "symbols": self.symbols,
            "completed_steps": list(self.completed_steps),
            "skipped_reason": self.skipped_reason,
        }


class AsyncPipelineHandler(Protocol):
    async def __call__(self, context: PipelineContext) -> Any: ...


def safe_summary(value: Any) -> Mapping[str, Any] | Any:
    """Return mapping-like values without logging or serializing secrets."""

    return value


__all__ = [
    "AsyncPipelineHandler",
    "EOD_STEP_ORDER",
    "Handler",
    "PipelineConfigurationError",
    "PipelineContext",
    "PipelineError",
    "PipelineResult",
    "PipelineStep",
    "PipelineStepError",
    "safe_summary",
]
