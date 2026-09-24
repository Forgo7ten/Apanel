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
        ("evaluate_all_alerts",),
    ),
)

NOTIFICATION_ENTRYPOINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "app.services.notification_service",
        ("summarize_deliveries",),
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
    notification_provider: Any | None = None,
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
        if notification_provider is not None:
            context.resources.setdefault("notification_provider", notification_provider)
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
    """Resolve the explicit alert service and adapt it to ``PipelineContext``."""

    service = _resolve_integration("alert", ALERT_ENTRYPOINTS)

    async def runner(context: PipelineContext) -> Any:
        return await _run_alert_service(service, context)

    return runner


def resolve_notification_runner() -> Integration:
    """Resolve the explicit notification summary service adapter."""

    service = _resolve_integration("notification", NOTIFICATION_ENTRYPOINTS)

    async def runner(context: PipelineContext) -> Any:
        return await _run_notification_service(service, context)

    return runner


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
    raise PipelineConfigurationError(f"{label} integration entrypoint is unavailable")


async def _run_alert_service(service: Callable[..., Any], context: PipelineContext) -> Any:
    if "session_factory" not in context.resources or context.resources["session_factory"] is None:
        raise PipelineConfigurationError("alert session factory is not configured")
    provider = context.resources.get("notification_provider")

    async def invoke(session: Any) -> Any:
        return await _invoke_callable(service, session, provider=provider)

    return await _run_in_session(context.resources["session_factory"], invoke)


async def _run_notification_service(
    service: Callable[..., Any],
    context: PipelineContext,
) -> Mapping[str, int]:
    if "session_factory" not in context.resources or context.resources["session_factory"] is None:
        raise PipelineConfigurationError("notification session factory is not configured")
    notification_ids = _notification_ids(context.artifacts.get("alerts"))

    async def invoke(session: Any) -> Any:
        return await _invoke_callable(service, session, notification_ids)

    summary = await _run_in_session(context.resources["session_factory"], invoke)
    if not isinstance(summary, Mapping):
        raise PipelineDataError("notification delivery summary is invalid")
    required = ("total", "sent", "failed", "pending")
    if any(key not in summary for key in required):
        raise PipelineDataError("notification delivery summary is incomplete")
    return {key: int(summary[key]) for key in required}


async def _run_in_session(session_factory: Any, operation: Callable[[Any], Any]) -> Any:
    async with session_factory() as session:
        try:
            result = await _invoke_callable(operation, session)
            await _invoke_callable(session.commit)
            return result
        except BaseException:
            rollback = getattr(session, "rollback", None)
            if rollback is not None:
                try:
                    await _invoke_callable(rollback)
                except Exception:
                    logger.warning(
                        "scheduled_pipeline_session_rollback_failed",
                        extra={"event": "scheduled_pipeline_session_rollback_failed"},
                    )
            raise


async def _invoke_callable(target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = target(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _notification_ids(value: Any) -> tuple[int, ...]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        raise PipelineDataError("alert evaluation results are unavailable")
    try:
        results = tuple(value)
    except TypeError as exc:
        raise PipelineDataError("alert evaluation results are invalid") from exc
    notification_ids: list[int] = []
    for result in results:
        if isinstance(result, Mapping):
            triggered = result.get("triggered")
            notification_id = result.get("notification_id")
        else:
            triggered = getattr(result, "triggered", None)
            notification_id = getattr(result, "notification_id", None)
        if not isinstance(triggered, bool):
            raise PipelineDataError("alert evaluation result is invalid")
        if triggered and notification_id is None:
            raise PipelineDataError("triggered alert has no notification record")
        if not triggered and notification_id is not None:
            raise PipelineDataError("inactive alert has a notification record")
        if notification_id is not None:
            try:
                normalized_id = int(notification_id)
            except (TypeError, ValueError) as exc:
                raise PipelineDataError("notification record id is invalid") from exc
            if normalized_id in notification_ids:
                raise PipelineDataError("alert batch contains duplicate notification records")
            notification_ids.append(normalized_id)
    return tuple(notification_ids)


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
