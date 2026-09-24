"""Injectable end-of-day orchestration and integration seams."""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from sqlalchemy import select

from app.models import Security, WatchTableSymbol
from app.repositories.indicator_state import IndicatorStateRepository
from app.services.indicator_service import IndicatorService
from app.services.state_service import StateService

from .contracts import (
    EOD_STEP_ORDER,
    PipelineConfigurationError,
    PipelineContext,
    PipelineResult,
    PipelineStep,
    PipelineStepError,
)
from .market_data import MarketDataClient

logger = logging.getLogger(__name__)


class PipelineDataError(RuntimeError):
    """A completed step did not make the next stage safe to execute."""


ALERT_ENTRYPOINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "app.services.alert_service",
        ("run_eod", "run_eod_pipeline", "evaluate_and_persist", "evaluate_alerts"),
    ),
    (
        "app.alerts.persistence",
        ("run_eod", "run_eod_pipeline", "evaluate_and_persist", "evaluate_alerts"),
    ),
    (
        "app.services.alert_persistence",
        ("run_eod", "run_eod_pipeline", "evaluate_and_persist", "evaluate_alerts"),
    ),
    (
        "app.services.alerts",
        ("run_eod", "run_eod_pipeline", "evaluate_and_persist", "evaluate_alerts"),
    ),
    (
        "app.repositories.alert",
        ("run_eod", "run_eod_pipeline", "evaluate_and_persist", "evaluate_alerts"),
    ),
)

NOTIFICATION_ENTRYPOINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "app.services.notification_service",
        ("run_eod", "send_pending", "send_notifications", "deliver_notifications"),
    ),
    (
        "app.providers.notification.service",
        ("run_eod", "send_pending", "send_notifications", "deliver_notifications"),
    ),
    (
        "app.services.notifications",
        ("run_eod", "send_pending", "send_notifications", "deliver_notifications"),
    ),
)


Integration = Callable[[PipelineContext], Awaitable[Any] | Any]


class EODPipeline:
    """Run exactly the product-defined seven-stage EOD sequence."""

    def __init__(self, steps: Iterable[PipelineStep]) -> None:
        resolved = tuple(steps)
        names = tuple(step.name for step in resolved)
        if names != EOD_STEP_ORDER:
            raise ValueError(f"EOD steps must be ordered as {EOD_STEP_ORDER!r}")
        self.steps = resolved

    async def run(self, context: PipelineContext) -> PipelineResult:
        completed: list[str] = []
        for step in self.steps:
            logger.info(
                "scheduled_pipeline_step_started",
                extra={
                    "event": "scheduled_pipeline_step_started",
                    "step": step.name,
                    "trade_date": context.trade_date.isoformat(),
                    "symbol_count": len(context.symbols),
                },
            )
            try:
                result = await step.run(context)
            except Exception as exc:
                # Avoid exception text here: HTTP/provider failures may contain
                # a webhook URL or an accidentally supplied credential.
                logger.error(
                    "scheduled_pipeline_step_failed",
                    extra={
                        "event": "scheduled_pipeline_step_failed",
                        "step": step.name,
                        "trade_date": context.trade_date.isoformat(),
                        "error_type": type(exc).__name__,
                    },
                )
                # Keep the original object on ``cause`` for in-process tests,
                # but suppress traceback chaining so workers cannot print a
                # provider exception containing a webhook/JWT value.
                raise PipelineStepError(step.name, exc) from None
            context.artifacts[step.name] = result
            completed.append(step.name)
            logger.info(
                "scheduled_pipeline_step_completed",
                extra={
                    "event": "scheduled_pipeline_step_completed",
                    "step": step.name,
                    "trade_date": context.trade_date.isoformat(),
                    "symbol_count": len(context.symbols),
                },
            )
        return PipelineResult(
            status="completed",
            trade_date=context.trade_date.isoformat(),
            adjustment=context.adjustment,
            symbols=len(context.symbols),
            completed_steps=tuple(completed),
        )


def build_default_eod_steps(
    *,
    session_factory: Any,
    market_data_client: MarketDataClient,
    alert_runner: Integration | None = None,
    notification_runner: Integration | None = None,
    delta_runner: Integration | None = None,
) -> tuple[PipelineStep, ...]:
    """Build production steps while leaving every external boundary injectable."""

    async def daily_sync(context: PipelineContext) -> Mapping[str, Any]:
        result = await market_data_client.sync_daily(
            symbols=context.symbols,
            start=context.trade_date,
            end=context.trade_date,
            adjustment=context.adjustment,
        )
        _require_success(result, "daily synchronization")
        return _safe_sync_summary(result)

    async def adjustment_ready(context: PipelineContext) -> Mapping[str, Any]:
        result = context.artifacts.get("daily_sync")
        _require_success(result, "adjustment data")
        return {"ready": True, "adjustment": context.adjustment}

    async def indicator_snapshots(context: PipelineContext) -> Mapping[str, int]:
        if session_factory is None:
            raise PipelineConfigurationError("indicator session factory is not configured")
        counts: dict[str, int] = {}
        for symbol in context.symbols:
            async with session_factory() as session:
                rows = await IndicatorService(session).calculate(
                    symbol,
                    adjustment=context.adjustment,
                )
            counts[symbol] = len(rows)
        return counts

    async def delta(context: PipelineContext) -> Any:
        if delta_runner is not None:
            return await _invoke_integration(delta_runner, context)
        # IndicatorService persists previous_values and delta atomically with
        # each upsert.  This explicit stage verifies that persistence boundary
        # before state recognition and is intentionally idempotent.
        if session_factory is None:
            raise PipelineConfigurationError("delta session factory is not configured")
        counts: dict[str, int] = {}
        for symbol in context.symbols:
            async with session_factory() as session:
                repository = IndicatorStateRepository(session)
                security = await repository.get_security(symbol)
                if security is None:
                    raise PipelineDataError("delta security data is not ready")
                snapshots = await repository.list_snapshots(security.id)
            counts[symbol] = sum(snapshot.delta is not None for snapshot in snapshots)
        return counts

    async def states(context: PipelineContext) -> Mapping[str, int]:
        if session_factory is None:
            raise PipelineConfigurationError("state session factory is not configured")
        counts: dict[str, int] = {}
        for symbol in context.symbols:
            async with session_factory() as session:
                rows = await StateService(session).calculate(
                    symbol,
                    adjustment=context.adjustment,
                )
            counts[symbol] = len(rows)
        return counts

    async def alerts(context: PipelineContext) -> Any:
        runner = alert_runner or resolve_alert_runner()
        return await _invoke_integration(runner, context)

    async def notifications(context: PipelineContext) -> Any:
        runner = notification_runner or resolve_notification_runner()
        return await _invoke_integration(runner, context)

    return (
        PipelineStep("daily_sync", daily_sync),
        PipelineStep("adjustment_ready", adjustment_ready),
        PipelineStep("indicator_snapshots", indicator_snapshots),
        PipelineStep("delta", delta),
        PipelineStep("states", states),
        PipelineStep("alerts", alerts),
        PipelineStep("notifications", notifications),
    )


async def _invoke_integration(target: Integration, context: PipelineContext) -> Any:
    result = target(context)
    if inspect.isawaitable(result):
        return await result
    return result


def resolve_alert_runner() -> Integration:
    """Resolve the parallel alert persistence entrypoint only when executed."""

    return _resolve_integration("alert", ALERT_ENTRYPOINTS)


def resolve_notification_runner() -> Integration:
    """Resolve the notification service boundary only when executed."""

    return _resolve_integration("notification", NOTIFICATION_ENTRYPOINTS)


def _resolve_integration(
    label: str,
    candidates: Iterable[tuple[str, Iterable[str]]],
) -> Integration:
    for module_name, attributes in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        except Exception as exc:
            raise PipelineConfigurationError(f"{label} integration could not be loaded") from exc
        for attribute in attributes:
            candidate = getattr(module, attribute, None)
            if callable(candidate):
                return candidate
            runner = getattr(candidate, "run", None)
            if callable(runner):
                return runner
    raise PipelineConfigurationError(f"{label} integration entrypoint is unavailable")


def _require_success(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        if "ok" in value and value["ok"] is not True:
            raise PipelineDataError(f"{label} did not complete")
        if "success" in value and value["success"] is not True:
            raise PipelineDataError(f"{label} did not complete")
    elif hasattr(value, "ok") and value.ok is not True:
        raise PipelineDataError(f"{label} did not complete")


def _safe_sync_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only non-sensitive synchronization counters in artifacts/logs."""

    allowed = ("operation", "total", "succeeded", "failed", "ok")
    return {key: value[key] for key in allowed if key in value}


async def resolve_watched_symbols(session_factory: Any) -> tuple[str, ...]:
    """Read the shared symbol universe without changing ORM ownership files."""

    if session_factory is None:
        raise PipelineConfigurationError("scheduler session factory is not configured")
    statement = (
        select(Security.symbol)
        .join(WatchTableSymbol, WatchTableSymbol.security_id == Security.id)
        .distinct()
        .order_by(Security.symbol.asc())
    )
    async with session_factory() as session:
        result = await session.execute(statement)
        return tuple(str(symbol) for symbol in result.scalars())


__all__ = [
    "ALERT_ENTRYPOINTS",
    "EODPipeline",
    "NOTIFICATION_ENTRYPOINTS",
    "PipelineDataError",
    "build_default_eod_steps",
    "resolve_alert_runner",
    "resolve_notification_runner",
    "resolve_watched_symbols",
]
