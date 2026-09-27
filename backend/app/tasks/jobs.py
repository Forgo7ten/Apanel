"""Synchronous Celery entrypoints around async, injectable scheduled work."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import date, datetime
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

from celery import Task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.market_data_hub import MarketDataHubClient, MarketDataHubClientProtocol
from app.core.config import Settings, get_settings
from app.db.redis import close_redis_client, create_redis_client
from app.db.session import create_engine, dispose_engine
from app.services.data_bootstrap_service import SecurityDataBootstrapService
from app.services.notification_service import dispatch_pending_notifications
from app.services.trading_calendar_service import TradingCalendarService

from .celery_app import celery_app
from .contracts import (
    PipelineConfigurationError,
    PipelineContext,
    PipelineError,
    PipelineRetryableDataError,
)
from .locks import RedisTaskLock
from .pipeline import (
    EODPipeline,
    build_default_eod_steps,
    resolve_monitored_symbols,
)

logger = logging.getLogger(__name__)


class ScheduledTaskError(RuntimeError):
    """Safe task-level error used after a retry budget is exhausted."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"scheduled task failed: {operation}")


class ScheduledTaskRetry(RuntimeError):
    """Safe error stored in Celery retry metadata."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"scheduled task retrying: {operation}")


def _today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _new_session_factory(settings: Settings) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_engine(
        settings.database_url,
        echo=settings.database_echo,
        connect_timeout_seconds=settings.database_connect_timeout_seconds,
        command_timeout_seconds=settings.database_command_timeout_seconds,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _summary(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        allowed = ("operation", "total", "succeeded", "failed", "ok")
        return {key: value[key] for key in allowed if key in value}
    return {
        "operation": getattr(value, "operation", "unknown"),
        "total": int(getattr(value, "total", 0)),
        "succeeded": int(getattr(value, "succeeded", 0)),
        "failed": int(getattr(value, "failed", 0)),
        "ok": bool(getattr(value, "ok", False)),
    }


def _ensure_ok(value: Any, operation: str) -> dict[str, Any]:
    result = _summary(value)
    if result.get("ok") is not True:
        raise PipelineError(f"{operation} did not complete")
    return result


async def _close_optional(value: Any) -> None:
    close = getattr(value, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def execute_quote_refresh(
    *,
    symbols: tuple[str, ...] | list[str] | None = None,
    settings: Settings | None = None,
    redis_client: Any | None = None,
    lock: Any | None = None,
    session_factory: Any | None = None,
    market_data_client: MarketDataHubClientProtocol | None = None,
) -> dict[str, Any]:
    """Refresh quotes once under a non-blocking distributed lease."""

    app_settings = settings or get_settings()
    app_settings.validate_database_credentials()
    if symbols is None:
        now = datetime.now(ZoneInfo(app_settings.timezone))
        minutes = now.hour * 60 + now.minute
        in_session = (9 * 60 + 30 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)
        calendar_engine = None
        calendar_factory = session_factory
        try:
            if calendar_factory is None:
                calendar_engine, calendar_factory = _new_session_factory(app_settings)
            async with calendar_factory() as calendar_session:
                if not in_session or not await TradingCalendarService(calendar_session).is_open(
                    now.date()
                ):
                    return {
                        "status": "skipped",
                        "reason": "market_closed",
                        "operation": "quote_refresh",
                    }
        finally:
            if calendar_engine is not None:
                await dispose_engine(calendar_engine)
    own_redis = redis_client is None and lock is None
    own_engine = session_factory is None
    own_client = market_data_client is None
    redis = redis_client
    engine = None
    client = market_data_client
    task_lock = lock
    try:
        if task_lock is None:
            redis = redis or create_redis_client(
                app_settings.redis_url,
                socket_connect_timeout_seconds=app_settings.redis_socket_connect_timeout_seconds,
                socket_timeout_seconds=app_settings.redis_socket_timeout_seconds,
            )
            task_lock = RedisTaskLock(
                redis,
                "apanel:task-lock:intraday-quotes",
                ttl_seconds=app_settings.scheduler_lock_ttl_seconds,
            )
        if not await task_lock.acquire():
            logger.info(
                "scheduled_task_skipped_lock",
                extra={"event": "scheduled_task_skipped_lock", "operation": "quote_refresh"},
            )
            return {"status": "skipped", "reason": "lock_held", "operation": "quote_refresh"}

        if symbols is None:
            if session_factory is None:
                engine, session_factory = _new_session_factory(app_settings)
            symbols = await resolve_monitored_symbols(session_factory)
        normalized_symbols = tuple(
            dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip())
        )
        if not normalized_symbols:
            return {"status": "skipped", "reason": "no_symbols", "operation": "quote_refresh"}
        if client is None:
            client = MarketDataHubClient(
                app_settings.market_data_hub_url,
                internal_api_token=app_settings.internal_api_token,
                timeout_seconds=app_settings.market_data_request_timeout_seconds,
                quote_timeout_seconds=app_settings.market_data_quote_timeout_seconds,
                daily_timeout_seconds=app_settings.market_data_daily_timeout_seconds,
                bootstrap_timeout_seconds=app_settings.market_data_bootstrap_timeout_seconds,
            )
        totals = {"total": 0, "succeeded": 0, "failed": 0}
        batch_size = app_settings.market_data_sync_batch_size
        for offset in range(0, len(normalized_symbols), batch_size):
            result = await client.sync_quotes(
                symbols=normalized_symbols[offset : offset + batch_size]
            )
            totals["total"] += result.total
            totals["succeeded"] += result.succeeded
            totals["failed"] += result.failed
            if result.retryable_symbols:
                raise PipelineError("quote synchronization has retryable failures")
        return {
            "status": "completed",
            "operation": "quote_refresh",
            "symbols": len(normalized_symbols),
            **totals,
            "ok": totals["failed"] == 0,
        }
    finally:
        if task_lock is not None:
            with suppress(Exception):
                await task_lock.release()
        if own_client and client is not None:
            with suppress(Exception):
                await _close_optional(client)
        if own_engine and engine is not None:
            with suppress(Exception):
                await dispose_engine(engine)
        if own_redis and redis is not None:
            with suppress(Exception):
                await close_redis_client(redis)


async def execute_eod_pipeline(
    *,
    trade_date: date | None = None,
    symbols: tuple[str, ...] | list[str] | None = None,
    adjustment: str = "qfq",
    settings: Settings | None = None,
    redis_client: Any | None = None,
    lock: Any | None = None,
    session_factory: Any | None = None,
    market_data_client: MarketDataHubClientProtocol | None = None,
    steps: tuple[Any, ...] | list[Any] | None = None,
    alert_runner: Any | None = None,
    notification_runner: Any | None = None,
    delta_runner: Any | None = None,
    notification_provider: Any | None = None,
) -> dict[str, Any]:
    """Run the strict seven-step EOD sequence under one distributed lease."""

    app_settings = settings or get_settings()
    app_settings.validate_database_credentials()
    selected_date = trade_date or _today(app_settings)
    if steps is None:
        calendar_engine = None
        calendar_factory = session_factory
        try:
            if calendar_factory is None:
                calendar_engine, calendar_factory = _new_session_factory(app_settings)
            async with calendar_factory() as calendar_session:
                if not await TradingCalendarService(calendar_session).is_open(selected_date):
                    return {
                        "status": "skipped",
                        "reason": "non_trading_day",
                        "operation": "eod_pipeline",
                        "trade_date": selected_date.isoformat(),
                    }
        finally:
            if calendar_engine is not None:
                await dispose_engine(calendar_engine)
    own_redis = redis_client is None and lock is None
    own_engine = session_factory is None and steps is None
    own_client = market_data_client is None and steps is None
    redis = redis_client
    engine = None
    client = market_data_client
    task_lock = lock
    try:
        if task_lock is None:
            redis = redis or create_redis_client(
                app_settings.redis_url,
                socket_connect_timeout_seconds=app_settings.redis_socket_connect_timeout_seconds,
                socket_timeout_seconds=app_settings.redis_socket_timeout_seconds,
            )
            task_lock = RedisTaskLock(
                redis,
                "apanel:task-lock:eod-pipeline",
                ttl_seconds=app_settings.scheduler_lock_ttl_seconds,
            )
        if not await task_lock.acquire():
            logger.info(
                "scheduled_task_skipped_lock",
                extra={"event": "scheduled_task_skipped_lock", "operation": "eod_pipeline"},
            )
            return {
                "status": "skipped",
                "reason": "lock_held",
                "operation": "eod_pipeline",
                "trade_date": selected_date.isoformat(),
            }

        if symbols is None:
            if session_factory is None:
                engine, session_factory = _new_session_factory(app_settings)
            symbols = await resolve_monitored_symbols(session_factory)
        normalized_symbols = tuple(
            dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip())
        )
        if not normalized_symbols:
            return {
                "status": "skipped",
                "reason": "no_symbols",
                "operation": "eod_pipeline",
                "trade_date": selected_date.isoformat(),
            }
        if steps is None:
            if session_factory is None:
                engine, session_factory = _new_session_factory(app_settings)
            if client is None:
                client = MarketDataHubClient(
                    app_settings.market_data_hub_url,
                    internal_api_token=app_settings.internal_api_token,
                    timeout_seconds=app_settings.market_data_request_timeout_seconds,
                    quote_timeout_seconds=app_settings.market_data_quote_timeout_seconds,
                    daily_timeout_seconds=app_settings.market_data_daily_timeout_seconds,
                    bootstrap_timeout_seconds=app_settings.market_data_bootstrap_timeout_seconds,
                )
            if hasattr(client, "validate_calendar"):
                validation = await client.validate_calendar(trade_date=selected_date)
                if validation.get("status") == "UNKNOWN":
                    raise PipelineConfigurationError(
                        "trading calendar actual validation is unknown"
                    )
                if validation.get("status") == "CLOSED":
                    return {
                        "status": "skipped",
                        "reason": "non_trading_day",
                        "operation": "eod_pipeline",
                        "trade_date": selected_date.isoformat(),
                    }
            steps = build_default_eod_steps(
                session_factory=session_factory,
                market_data_client=client,
                alert_runner=alert_runner,
                notification_runner=notification_runner,
                delta_runner=delta_runner,
                notification_provider=notification_provider,
            )
        context = PipelineContext(
            trade_date=selected_date,
            adjustment=adjustment,
            symbols=normalized_symbols,
            resources={
                "settings": app_settings,
                "session_factory": session_factory,
                "market_data_client": client,
                "notification_provider": notification_provider,
                "analysis_adjustments": (
                    ("qfq", "none") if app_settings.enable_none_analysis else (adjustment,)
                ),
            },
        )
        result = await EODPipeline(steps).run(context)
        retryable_issues = tuple(issue for issue in result.issues if issue.retryable)
        if retryable_issues:
            raise PipelineRetryableDataError(retryable_issues)
        return result.to_dict()
    finally:
        if task_lock is not None:
            with suppress(Exception):
                await task_lock.release()
        if own_client and client is not None:
            with suppress(Exception):
                await _close_optional(client)
        if own_engine and engine is not None:
            with suppress(Exception):
                await dispose_engine(engine)
        if own_redis and redis is not None:
            with suppress(Exception):
                await close_redis_client(redis)


def _retry_or_raise(task: Task, operation: str, exc: Exception) -> NoReturn:
    settings = get_settings()
    retry_count = int(getattr(task.request, "retries", 0))
    if retry_count < settings.scheduler_retry_max_attempts:
        countdown = settings.scheduler_retry_backoff_seconds * (2**retry_count)
        logger.warning(
            "scheduled_task_retry",
            extra={
                "event": "scheduled_task_retry",
                "operation": operation,
                "retry_count": retry_count + 1,
                "error_type": type(exc).__name__,
            },
        )
        raise task.retry(
            exc=ScheduledTaskRetry(operation),
            countdown=countdown,
        )
    # Celery's final failure log must contain only this safe operation label,
    # never the original provider/webhook exception text.
    raise ScheduledTaskError(operation) from None


@celery_app.task(
    bind=True,
    name="apanel.tasks.refresh_quotes",
    max_retries=None,
)
def refresh_quotes(task: Task, symbols: list[str] | None = None) -> dict[str, Any]:
    """Celery entrypoint for the 5--10 minute quote refresh."""

    try:
        return asyncio.run(
            execute_quote_refresh(symbols=None if symbols is None else tuple(symbols))
        )
    except Exception as exc:
        _retry_or_raise(task, "quote_refresh", exc)


@celery_app.task(
    bind=True,
    name="apanel.tasks.run_eod_pipeline",
    max_retries=None,
)
def run_eod_pipeline(
    task: Task,
    trade_date: str | None = None,
    adjustment: str = "qfq",
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Celery entrypoint for the post-close indicator/state/alert pipeline."""

    selected_date = date.fromisoformat(trade_date) if trade_date else None
    try:
        return asyncio.run(
            execute_eod_pipeline(
                trade_date=selected_date,
                adjustment=adjustment,
                symbols=None if symbols is None else tuple(symbols),
            )
        )
    except Exception as exc:
        _retry_or_raise(task, "eod_pipeline", exc)


async def execute_security_bootstrap(
    symbol: str,
    *,
    settings: Settings | None = None,
    session_factory: Any | None = None,
    market_data_client: MarketDataHubClientProtocol | None = None,
    redis_client: Any | None = None,
) -> dict[str, Any]:
    app_settings = settings or get_settings()
    own_engine = session_factory is None
    own_client = market_data_client is None
    own_redis = redis_client is None
    engine = None
    client = market_data_client
    redis = redis_client
    task_lock = None
    try:
        redis = redis or create_redis_client(
            app_settings.redis_url,
            socket_connect_timeout_seconds=app_settings.redis_socket_connect_timeout_seconds,
            socket_timeout_seconds=app_settings.redis_socket_timeout_seconds,
        )
        task_lock = RedisTaskLock(
            redis,
            f"apanel:task-lock:bootstrap-security-data:{symbol.strip().upper()}",
            ttl_seconds=app_settings.scheduler_lock_ttl_seconds,
        )
        if not await task_lock.acquire():
            return {
                "status": "skipped",
                "reason": "lock_held",
                "operation": "security_bootstrap",
                "symbol": symbol,
            }
        if session_factory is None:
            engine, session_factory = _new_session_factory(app_settings)
        if client is None:
            client = MarketDataHubClient(
                app_settings.market_data_hub_url,
                internal_api_token=app_settings.internal_api_token,
                timeout_seconds=app_settings.market_data_request_timeout_seconds,
                quote_timeout_seconds=app_settings.market_data_quote_timeout_seconds,
                daily_timeout_seconds=app_settings.market_data_daily_timeout_seconds,
                bootstrap_timeout_seconds=app_settings.market_data_bootstrap_timeout_seconds,
            )
        async with session_factory() as session:
            from app.tasks.pipeline import collect_indicator_requests

            requests = (await collect_indicator_requests(session, (symbol,))).get(symbol)
        result = await SecurityDataBootstrapService(
            session_factory=session_factory,
            market_data_client=client,
            minimum_bars=app_settings.analysis_bootstrap_min_bars,
        ).ensure_ready(
            symbol,
            adjustments=("qfq", "none") if app_settings.enable_none_analysis else ("qfq",),
            requests=requests,
        )
        return {
            "status": "completed",
            "operation": "security_bootstrap",
            "symbol": result.symbol,
            "indicators": result.indicators_materialized,
            "states": result.states_materialized,
            "dividend_synced": result.dividend_synced,
        }
    finally:
        if task_lock is not None:
            with suppress(Exception):
                await task_lock.release()
        if own_client and client is not None:
            with suppress(Exception):
                await _close_optional(client)
        if own_engine and engine is not None:
            with suppress(Exception):
                await dispose_engine(engine)
        if own_redis and redis is not None:
            with suppress(Exception):
                await close_redis_client(redis)


@celery_app.task(bind=True, name="apanel.tasks.bootstrap_security_data", max_retries=None)
def bootstrap_security_data(task: Task, symbol: str) -> dict[str, Any]:
    try:
        return asyncio.run(execute_security_bootstrap(symbol))
    except Exception as exc:
        _retry_or_raise(task, "security_bootstrap", exc)


def enqueue_security_bootstrap(symbol: str) -> None:
    """Best-effort API-side enqueue; EOD readiness remains the recovery path."""
    if get_settings().app_env.casefold() == "test":
        return
    try:
        bootstrap_security_data.delay(symbol)
    except Exception:
        logger.warning(
            "security_bootstrap_enqueue_failed",
            extra={"event": "security_bootstrap_enqueue_failed", "symbol": symbol},
        )


async def execute_pending_notification_dispatch(
    *, settings: Settings | None = None
) -> dict[str, int]:
    app_settings = settings or get_settings()
    engine, session_factory = _new_session_factory(app_settings)
    try:
        async with session_factory() as session:
            return await dispatch_pending_notifications(session)
    finally:
        await dispose_engine(engine)


@celery_app.task(bind=True, name="apanel.tasks.dispatch_pending_notifications", max_retries=None)
def dispatch_pending_notifications_task(task: Task) -> dict[str, int]:
    try:
        return asyncio.run(execute_pending_notification_dispatch())
    except Exception as exc:
        _retry_or_raise(task, "notification_dispatch", exc)


# Descriptive aliases for callers that prefer an explicit task suffix.
refresh_quotes_task = refresh_quotes
run_eod_pipeline_task = run_eod_pipeline


__all__ = [
    "ScheduledTaskError",
    "ScheduledTaskRetry",
    "execute_eod_pipeline",
    "execute_security_bootstrap",
    "enqueue_security_bootstrap",
    "execute_quote_refresh",
    "execute_pending_notification_dispatch",
    "refresh_quotes",
    "refresh_quotes_task",
    "run_eod_pipeline",
    "run_eod_pipeline_task",
    "bootstrap_security_data",
    "dispatch_pending_notifications_task",
]
