from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.tasks.contracts import (
    EOD_STEP_ORDER,
    PipelineConfigurationError,
    PipelineContext,
    PipelineStep,
    PipelineStepError,
)
from app.tasks.jobs import _retry_or_raise, execute_eod_pipeline
from app.tasks.pipeline import EODPipeline, build_default_eod_steps, resolve_alert_runner
from app.tasks.schedule import build_beat_schedule


def test_schedule_uses_shanghai_trading_sessions_and_env_overrides() -> None:
    settings = Settings(
        quote_refresh_interval_minutes=10,
        eod_pipeline_hour=16,
        eod_pipeline_minute=5,
    )

    schedule = build_beat_schedule(settings)
    quote = schedule["refresh-intraday-quotes"]["schedule"]
    eod = schedule["run-end-of-day-pipeline"]["schedule"]

    assert quote.minute == {0, 10, 20, 30, 40, 50}
    assert quote.hour == {9, 10, 11, 13, 14}
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
    result = await EODPipeline(steps).run(
        PipelineContext(date(2026, 9, 24), "qfq", ("600519",))
    )

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
    assert calls == ["daily_sync", "adjustment_ready", "indicator_snapshots", "delta"]
    assert "secret.example" not in str(error.value)


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
