"""Cross-transaction publication tests on a disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    AnalysisStream,
    CurrentIndicatorState,
    DailyBar,
    IndicatorSnapshot,
    StateEvent,
    TradingCalendar,
)
from app.repositories.indicator_state import IndicatorStateRepository
from app.services.analysis_service import AnalysisService
from tests.test_alert_postgres_concurrency import _seed
from tests.test_auth_postgres_concurrency import postgres_engine as postgres_engine
from tests.test_auth_postgres_concurrency import (
    postgres_session_factory as postgres_session_factory,
)


async def seed_series(factory):
    await _seed(factory)
    async with factory() as session:
        first = date(2026, 1, 1)
        stream = await session.get(AnalysisStream, (1, "qfq"))
        stream.source_revision = 1
        stream.dirty_from = first
        for i in range(40):
            day = first + timedelta(days=i)
            session.add(
                TradingCalendar(
                    market="CN",
                    trade_date=day,
                    status="OPEN",
                    source="TEST",
                    expected_open=True,
                    actual_open=True,
                    source_metadata={},
                )
            )
            value = Decimal(120 - i if i < 20 else 100 + i - 20)
            session.add(
                DailyBar(
                    security_id=1,
                    adjust_type="qfq",
                    trade_date=day,
                    open=value,
                    high=value + 1,
                    low=value - 1,
                    close=value,
                    volume=100,
                )
            )
        await session.commit()
    return first + timedelta(days=39)


async def test_two_workers_publish_one_generation_and_do_not_duplicate_events(
    postgres_session_factory,
):
    factory = postgres_session_factory
    last = await seed_series(factory)
    barrier = asyncio.Barrier(2)

    async def materialize():
        async with factory() as session:
            await barrier.wait()
            return await AnalysisService(session).materialize("600519", end=last)

    results = await asyncio.wait_for(asyncio.gather(materialize(), materialize()), 30)
    assert sum(item.indicators > 0 for item in results) == 1
    async with factory() as session:
        stream = (
            await session.execute(select(AnalysisStream).where(AnalysisStream.adjust_type == "qfq"))
        ).scalar_one()
        assert stream.published_revision == 1 and stream.dirty_from is None
        assert stream.state_through == last
        events = (await session.execute(select(StateEvent))).scalars().all()
        assert stream.event_sequence == len(events)
        assert len({row.stream_seq for row in events}) == len(events)
        assert (
            await session.execute(select(func.count()).select_from(CurrentIndicatorState))
        ).scalar_one() == 18


async def test_event_and_indicator_writes_roll_back_if_current_snapshot_write_fails(
    postgres_session_factory, monkeypatch
):
    factory = postgres_session_factory
    last = await seed_series(factory)
    original = IndicatorStateRepository._upsert_many

    async def fail_current(self, model, *args, **kwargs):
        if model is CurrentIndicatorState:
            raise RuntimeError("injected current state write failure")
        return await original(self, model, *args, **kwargs)

    monkeypatch.setattr(IndicatorStateRepository, "_upsert_many", fail_current)
    async with factory() as session:
        with pytest.raises(RuntimeError):
            await AnalysisService(session).materialize("600519", end=last)
    async with factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(StateEvent))
        ).scalar_one() == 0
        assert (
            await session.execute(select(func.count()).select_from(CurrentIndicatorState))
        ).scalar_one() == 0
        # _seed provided one legacy RSI snapshot before the transaction.
        assert (
            await session.execute(select(func.count()).select_from(IndicatorSnapshot))
        ).scalar_one() == 1
        stream = (
            await session.execute(select(AnalysisStream).where(AnalysisStream.adjust_type == "qfq"))
        ).scalar_one()
        assert stream.dirty_from == date(2026, 1, 1)
        assert stream.published_revision == -1 and stream.event_sequence == 0
