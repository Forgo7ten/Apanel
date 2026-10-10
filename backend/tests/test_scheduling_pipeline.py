from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.indicators.parameters import IndicatorRequest
from app.tasks.contracts import (
    EOD_STEP_ORDER,
    PipelineConfigurationError,
    PipelineContext,
    PipelineIssue,
    PipelineRetryableDataError,
    PipelineStep,
    PipelineStepError,
)
from app.tasks.jobs import (
    _retry_or_raise,
    _run_with_lock_lease,
    _validate_missing_calendar_date,
    execute_eod_pipeline,
)
from app.tasks.locks import TaskLockError
from app.tasks.pipeline import (
    EODPipeline,
    _sync_in_chunks,
    _sync_required_history_in_chunks,
    build_default_eod_steps,
    resolve_alert_runner,
)
from app.tasks.schedule import build_beat_schedule


def test_schedule_uses_shanghai_trading_sessions_and_env_overrides() -> None:
    settings = Settings(
        quote_refresh_interval_minutes=10,
        eod_pipeline_hour=16,
        eod_pipeline_minute=5,
    )

    schedule = build_beat_schedule(settings)
    quote_open = schedule["refresh-quotes-open"]["schedule"]
    quote_lunch = schedule["refresh-quotes-before-lunch"]["schedule"]
    quote_close = schedule["refresh-quotes-close"]["schedule"]
    eod = schedule["run-end-of-day-pipeline"]["schedule"]

    assert quote_open.hour == {9}
    assert quote_lunch.hour == {11}
    assert quote_close.hour == {15}
    assert quote_close.minute == {0}
    assert eod.hour == {16}
    assert eod.minute == {5}


@pytest.mark.asyncio
async def test_eod_pipeline_is_strictly_ordered() -> None:
    calls: list[str] = []

    async def handler(context: PipelineContext, name: str) -> None:
        calls.append(name)

    steps = tuple(
        PipelineStep(name, lambda context, name=name: handler(context, name))
        for name in EOD_STEP_ORDER
    )
    result = await EODPipeline(steps).run(PipelineContext(date(2026, 9, 24), "qfq", ("600519",)))

    assert tuple(calls) == EOD_STEP_ORDER
    assert result.completed_steps == EOD_STEP_ORDER


@pytest.mark.asyncio
async def test_eod_pipeline_stops_after_the_first_failure() -> None:
    calls: list[str] = []

    async def good(context: PipelineContext, name: str) -> None:
        calls.append(name)

    async def fail(_context: PipelineContext) -> None:
        calls.append("delta")
        raise RuntimeError("webhook=https://secret.example/token")

    steps = tuple(
        PipelineStep(
            name,
            fail if name == "delta" else lambda context, name=name: good(context, name),
        )
        for name in EOD_STEP_ORDER
    )

    with pytest.raises(PipelineStepError) as error:
        await EODPipeline(steps).run(PipelineContext(date(2026, 9, 24), "qfq", ("600519",)))

    assert error.value.step == "delta"
    assert calls == [
        "daily_sync",
        "dividend_sync",
        "adjustment_ready",
        "indicator_snapshots",
        "delta",
    ]
    assert "secret.example" not in str(error.value)


@pytest.mark.asyncio
async def test_eod_pipeline_reports_permanent_partial_failure_after_all_steps() -> None:
    calls: list[str] = []

    async def handler(context: PipelineContext, name: str) -> None:
        calls.append(name)
        if name == "daily_sync":
            context.resources["pipeline_issues"] = [
                PipelineIssue(
                    step="daily_sync",
                    symbol="600519",
                    adjustment="qfq",
                    code="INVALID_MARKET_DATA",
                    retryable=False,
                )
            ]

    steps = tuple(
        PipelineStep(name, lambda context, name=name: handler(context, name))
        for name in EOD_STEP_ORDER
    )
    result = await EODPipeline(steps).run(
        PipelineContext(date(2026, 9, 24), "qfq", ("600519", "000001"))
    )

    assert tuple(calls) == EOD_STEP_ORDER
    assert result.status == "completed_with_errors"
    assert result.issues[0].code == "INVALID_MARKET_DATA"
    assert result.to_dict()["issues"][0]["symbol"] == "600519"


@pytest.mark.asyncio
async def test_execute_eod_retries_only_after_healthy_steps_finish() -> None:
    calls: list[str] = []

    async def handler(context: PipelineContext, name: str) -> None:
        calls.append(name)
        if name == "daily_sync":
            context.resources["pipeline_issues"] = [
                PipelineIssue(
                    step="daily_sync",
                    symbol="600519",
                    adjustment="qfq",
                    code="PROVIDER_TIMEOUT",
                    retryable=True,
                )
            ]

    steps = tuple(
        PipelineStep(name, lambda context, name=name: handler(context, name))
        for name in EOD_STEP_ORDER
    )

    with pytest.raises(PipelineRetryableDataError):
        await execute_eod_pipeline(
            trade_date=date(2026, 9, 24),
            symbols=["600519", "000001"],
            lock=SimpleNamespace(acquire=lambda: _true_async(), release=lambda: _noop_async()),
            steps=steps,
            settings=Settings(),
        )

    assert tuple(calls) == EOD_STEP_ORDER


@pytest.mark.asyncio
async def test_sync_in_chunks_never_reports_failed_items_as_ok() -> None:
    from app.clients.market_data_hub import SyncItem, SyncResult

    class Client:
        async def sync_daily(self, **_kwargs):
            return SyncResult(
                operation="daily",
                items=(
                    SyncItem("600519", "success"),
                    SyncItem("000001", "failed", error_code="PROVIDER_TIMEOUT"),
                    SyncItem("300750", "failed", error_code="INVALID_MARKET_DATA"),
                    SyncItem("601318", "failed", error_code="PERSISTENCE_ERROR"),
                ),
            )

    context = PipelineContext(date(2026, 9, 24), "qfq", ("600519", "000001", "300750", "601318"))
    context.resources["market_data_client"] = Client()
    context.resources["settings"] = Settings(market_data_sync_batch_size=50)

    result = await _sync_in_chunks(context, "daily", adjustment="qfq")

    assert result["ok"] is False
    assert result["failed"] == 3
    assert result["retryable_symbols"] == ("000001", "601318")
    assert result["permanent_failures"] == ("300750",)


@pytest.mark.asyncio
async def test_eod_history_sync_repairs_required_provider_windows_without_calendar_gaps() -> None:
    from app.clients.market_data_hub import SyncItem, SyncResult

    class Client:
        def __init__(self) -> None:
            self.calls = []

        async def sync_daily(self, **kwargs):
            self.calls.append(kwargs)
            return SyncResult(
                operation="daily",
                items=tuple(SyncItem(symbol, "success") for symbol in kwargs["symbols"]),
            )

    client = Client()
    context = PipelineContext(date(2026, 9, 24), "qfq", ("600519", "000001"))
    context.resources["market_data_client"] = client
    context.resources["settings"] = Settings(market_data_sync_batch_size=50)

    result = await _sync_required_history_in_chunks(
        context,
        adjustment="qfq",
        requests_by_symbol={
            "600519": (),
            "000001": (IndicatorRequest("MA", {"period": 60}),),
        },
        minimum_bars=1,
    )

    assert result["ok"] is True
    assert [(call["symbols"], call["lookback_bars"], call["end"]) for call in client.calls] == [
        (["600519"], 34, date(2026, 9, 24)),
        (["000001"], 60, date(2026, 9, 24)),
    ]


@pytest.mark.asyncio
async def test_adjustment_ready_treats_missing_target_bar_as_suspension_not_retry() -> None:
    class ScalarRows:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, _statement):
            return ScalarRows()

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Factory:
        def __call__(self):
            return SessionContext()

    class Client:
        pass

    async def runner(_context):
        return []

    steps = build_default_eod_steps(
        session_factory=Factory(),
        market_data_client=Client(),
        alert_runner=runner,
        notification_runner=runner,
    )
    context = PipelineContext(date(2026, 9, 24), "qfq", ("600519",))
    context.resources["analysis_adjustments"] = ("qfq",)

    result = await steps[2].run(context)

    assert result["ready_symbols"] == {"qfq": ()}
    assert result["no_bar_symbols"] == {"qfq": ("600519",)}
    assert context.resources.get("pipeline_issues", []) == []


@pytest.mark.asyncio
async def test_execute_eod_releases_lock_when_a_step_fails() -> None:
    class FakeLock:
        def __init__(self) -> None:
            self.acquired = 0
            self.released = 0

        async def acquire(self) -> bool:
            self.acquired += 1
            return True

        async def release(self) -> None:
            self.released += 1

    lock = FakeLock()

    async def fail(_context: PipelineContext) -> None:
        raise RuntimeError("failure")

    steps = tuple(
        PipelineStep(name, fail if name == "states" else (lambda _context: None))
        for name in EOD_STEP_ORDER
    )
    with pytest.raises(PipelineStepError):
        await execute_eod_pipeline(
            trade_date=date(2026, 9, 24),
            symbols=["600519"],
            lock=lock,
            steps=steps,
            settings=Settings(),
        )

    assert lock.acquired == 1
    assert lock.released == 1


@pytest.mark.asyncio
async def test_lock_guard_cancels_pipeline_work_when_lease_is_lost() -> None:
    cancelled = False

    class Lock:
        async def wait_lost(self):
            await _noop_async()

    async def work():
        nonlocal cancelled
        try:
            await __import__("asyncio").sleep(60)
        except __import__("asyncio").CancelledError:
            cancelled = True
            raise

    with pytest.raises(TaskLockError, match="lease was lost"):
        await _run_with_lock_lease(Lock(), work())

    assert cancelled is True


def test_missing_alert_entrypoint_is_explicit_and_lazy() -> None:
    with patch("app.tasks.pipeline.importlib.import_module", side_effect=ModuleNotFoundError):
        with pytest.raises(PipelineConfigurationError, match="alert integration"):
            resolve_alert_runner()


def test_retry_uses_backoff_without_logging_exception_text() -> None:
    class RetrySentinel(Exception):
        pass

    class FakeTask:
        request = SimpleNamespace(retries=1)

        def retry(self, *, exc, countdown):
            assert str(exc) == "scheduled task retrying: eod_pipeline"
            assert countdown == 120
            raise RetrySentinel

    with pytest.raises(RetrySentinel):
        _retry_or_raise(FakeTask(), "eod_pipeline", RuntimeError("JWT=secret"))


@pytest.mark.asyncio
async def test_missing_annual_calendar_backfills_recent_actual_dates() -> None:
    calls: list[date] = []

    class Client:
        async def validate_calendar(self, *, trade_date):
            calls.append(trade_date)
            return {"status": "OPEN" if trade_date == date(2027, 1, 4) else "CLOSED"}

    status = await _validate_missing_calendar_date(
        date(2027, 1, 4),
        settings=Settings(),
        market_data_client=Client(),
    )

    assert status == "OPEN"
    assert calls[0] == date(2027, 1, 4) - timedelta(days=14)
    assert calls[-1] == date(2027, 1, 4)
    assert len(calls) == 15


@pytest.mark.asyncio
async def test_missing_annual_calendar_remains_fail_closed_when_actual_is_unknown() -> None:
    class Client:
        async def validate_calendar(self, *, trade_date):
            return {"status": "UNKNOWN" if trade_date == date(2027, 1, 4) else "CLOSED"}

    with pytest.raises(PipelineConfigurationError, match="calendar actual validation"):
        await _validate_missing_calendar_date(
            date(2027, 1, 4),
            settings=Settings(),
            market_data_client=Client(),
        )


def test_custom_default_steps_keep_alert_and_notification_boundaries_injected() -> None:
    class Client:
        async def sync_daily(self, **kwargs):
            return {"ok": True}

    async def runner(_context: PipelineContext):
        return {"ok": True}

    steps = build_default_eod_steps(
        session_factory=object(),
        market_data_client=Client(),
        alert_runner=runner,
        notification_runner=runner,
    )
    assert tuple(step.name for step in steps) == EOD_STEP_ORDER


@pytest.mark.asyncio
async def test_eod_pipeline_accepts_none_as_an_explicit_analysis_adjustment() -> None:
    calls: list[str] = []

    async def handler(_context: PipelineContext, name: str) -> None:
        calls.append(name)

    steps = tuple(
        PipelineStep(name, lambda context, name=name: handler(context, name))
        for name in EOD_STEP_ORDER
    )
    result = await execute_eod_pipeline(
        trade_date=date(2026, 9, 24),
        symbols=["600519"],
        adjustment="none",
        lock=SimpleNamespace(acquire=lambda: _true_async(), release=lambda: _noop_async()),
        steps=steps,
        settings=Settings(),
    )
    assert result["status"] == "completed"
    assert tuple(calls) == EOD_STEP_ORDER


async def _true_async() -> bool:
    return True


async def _noop_async() -> None:
    return None


@pytest.mark.asyncio
async def test_outer_cancellation_joins_protected_work_before_releasing_lease():
    import asyncio

    started, cancelled = asyncio.Event(), asyncio.Event()

    class Lock:
        async def wait_lost(self):
            await asyncio.Event().wait()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runner = asyncio.create_task(_run_with_lock_lease(Lock(), work()))
    await started.wait()
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    assert cancelled.is_set()
