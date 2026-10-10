"""Real PostgreSQL interleavings for stream publication, configuration and Alert replay."""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select, text

from app.models import AlertInstance, AlertRule, AnalysisStream, Notification
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import AlertService
from app.services.analysis_control import lock_analysis_stream, mark_demand_changed
from app.services.analysis_service import AnalysisService
from app.services.indicator_service import IndicatorService
from tests.test_alert_postgres_concurrency import _seed
from tests.test_auth_postgres_concurrency import postgres_engine as postgres_engine
from tests.test_auth_postgres_concurrency import (
    postgres_session_factory as postgres_session_factory,
)
from tests.test_state_events import publish, row
from tests.test_state_events_postgres import seed_series


async def seed_catchup(factory):
    uid, _ = await _seed(factory)
    first = date(2026, 9, 24)
    async with factory() as session:
        await session.execute(delete(AlertRule))
        await session.commit()
        rule = await AlertService(session).create(
            uid,
            AlertRuleCreateRequest(
                security_id=1, condition_type="STATE", state_id="BOLL_WIDTH_NARROWING"
            ),
        )
        await publish(session, 1, [row(1, first, "INACTIVE")])
        await AlertService(session).evaluate_all(observation_date=first)
        await publish(
            session,
            1,
            [
                row(1, first + timedelta(days=1), "ACTIVE", transition=True),
                row(1, first + timedelta(days=2), "INACTIVE"),
                row(1, first + timedelta(days=3), "ACTIVE", transition=True),
            ],
            start=first + timedelta(days=1),
        )
        return rule.id, first, first + timedelta(days=3)


async def test_concurrent_catchup_claims_each_enter_once(postgres_session_factory):
    factory = postgres_session_factory
    rid, _, last = await seed_catchup(factory)
    barrier = asyncio.Barrier(2)

    async def consume():
        async with factory() as session:
            await barrier.wait()
            return await AlertService(session).evaluate_all(observation_date=last)

    results = await asyncio.wait_for(asyncio.gather(consume(), consume()), 15)
    assert sum(len(batch[0].notification_ids) for batch in results) == 2
    async with factory() as session:
        records = (await session.execute(select(Notification))).scalars().all()
        assert len(records) == 2
        assert len({item.content["source_event_id"] for item in records}) == 2
        instance = (
            await session.execute(select(AlertInstance).where(AlertInstance.alert_rule_id == rid))
        ).scalar_one()
        stream = await session.get(AnalysisStream, (1, "qfq"))
        assert instance.last_evaluated_trade_date == last
        assert instance.last_consumed_state_seq == stream.event_sequence


async def test_catchup_failure_rolls_back_outbox_and_cursor_together(
    postgres_session_factory, monkeypatch
):
    factory = postgres_session_factory
    rid, first, last = await seed_catchup(factory)
    original = AlertService._enqueue_trigger
    calls = 0

    async def fail_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure after first outbox flush")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AlertService, "_enqueue_trigger", fail_second)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="injected"):
            await AlertService(session).evaluate_all(observation_date=last)
        await session.rollback()
    async with factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(Notification))
        ).scalar_one() == 0
        instance = (
            await session.execute(select(AlertInstance).where(AlertInstance.alert_rule_id == rid))
        ).scalar_one()
        assert instance.last_evaluated_trade_date == first
    monkeypatch.setattr(AlertService, "_enqueue_trigger", original)
    async with factory() as session:
        results = await AlertService(session).evaluate_all(observation_date=last)
        assert len(results[0].notification_ids) == 2


@pytest.mark.parametrize("change", ["demand", "source"])
async def test_change_waiting_on_publication_keeps_its_invalidation(
    postgres_session_factory, monkeypatch, change
):
    factory = postgres_session_factory
    last = await seed_series(factory)
    entered, release, writer_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = IndicatorService.rebuild_history
    writer_pid = None

    async def pause_under_stream_lock(self, *args, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(IndicatorService, "rebuild_history", pause_under_stream_lock)

    async def analyze():
        async with factory() as session:
            return await AnalysisService(session).materialize("600519", end=last)

    async def invalidate():
        nonlocal writer_pid
        async with factory() as session:
            writer_pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            writer_ready.set()
            if change == "demand":
                await mark_demand_changed(session, [1])
            else:
                stream = await lock_analysis_stream(session, 1, "qfq")
                stream.source_revision += 1
                stream.dirty_from = last
            await session.commit()

    reader_task = asyncio.create_task(analyze())
    writer_task = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        writer_task = asyncio.create_task(invalidate())
        await asyncio.wait_for(writer_ready.wait(), 5)
        # Observe an actual database lock wait, rather than assuming that a
        # short sleep implies the concurrent transaction reached the boundary.
        async with factory() as probe:
            async with asyncio.timeout(5):
                while True:
                    waiting = (
                        await probe.execute(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                            {"pid": writer_pid},
                        )
                    ).scalar_one()
                    await probe.commit()  # refresh pg_stat_activity's per-transaction snapshot
                    if waiting == "Lock":
                        break
                    await asyncio.sleep(0.02)
        assert not writer_task.done()
    finally:
        release.set()
        tasks = [reader_task] + ([writer_task] if writer_task is not None else [])
        await asyncio.wait_for(asyncio.gather(*tasks), 15)
    async with factory() as session:
        stream = await session.get(AnalysisStream, (1, "qfq"))
        if change == "demand":
            assert stream.demand_generation > stream.published_demand_generation
        else:
            assert stream.source_revision > stream.published_revision
            assert stream.dirty_from == last
