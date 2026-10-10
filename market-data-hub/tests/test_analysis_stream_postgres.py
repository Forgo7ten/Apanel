"""Real PostgreSQL facts/fence/dirty transaction tests; never use a production URL."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.market_data import Adjustment
from app.models.market_data import (
    AnalysisStreamModel,
    Base,
    DailyBarModel,
    MarketDataAdjustmentStateModel,
    SecurityModel,
)
from app.repositories.market_data import PersistenceRecordError, SqlAlchemyDailyBarRepository
from tests.test_sync_services import bar


@pytest_asyncio.fixture
async def pg_factory():
    url = os.environ.get("APANEL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set APANEL_TEST_POSTGRES_URL to a disposable PostgreSQL database")
    assert url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            SecurityModel(
                symbol="600519",
                name="Stream",
                market="SH",
                exchange="SH",
                security_type="STOCK",
                status="ACTIVE",
            )
        )
        await session.commit()
    yield factory
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_changed_fact_and_dirty_range_are_atomic_and_unchanged_upserts_are_noops(pg_factory):
    repository = SqlAlchemyDailyBarRepository(pg_factory)
    records = [replace(bar("600519", day), adjustment=Adjustment.QFQ) for day in (2, 3, 4)]
    generation = await repository.reserve_sync(["600519"], adjustment="qfq")
    await repository.upsert_fenced(records, generations=generation, revisions={"600519": "v1"})
    async with pg_factory() as session:
        stream = (await session.execute(select(AnalysisStreamModel))).scalar_one()
        assert stream.source_revision == 1
        assert stream.dirty_from == date(2026, 9, 2)
        descriptor = (await session.execute(select(MarketDataAdjustmentStateModel))).scalar_one()
        assert descriptor.revision_hash == "v1"
        stream.dirty_from = None
        stream.published_revision = 1
        await session.commit()
    # Same values neither churn facts nor advance source revision.
    await repository.upsert_fenced(records, generations=generation, revisions={"600519": "v1"})
    async with pg_factory() as session:
        stream = (await session.execute(select(AnalysisStreamModel))).scalar_one()
        assert stream.source_revision == 1 and stream.dirty_from is None
    corrected = replace(records[1], high=records[1].high + 1)
    await repository.upsert_fenced([corrected], generations=generation, revisions={"600519": "v1"})
    async with pg_factory() as session:
        stream = (await session.execute(select(AnalysisStreamModel))).scalar_one()
        assert stream.source_revision == 2
        assert stream.dirty_from == date(2026, 9, 3)
        assert stream.latest_bar_date == date(2026, 9, 4)


async def test_stale_fetch_cannot_overwrite_newer_sync(pg_factory):
    repository = SqlAlchemyDailyBarRepository(pg_factory)
    older = await repository.reserve_sync(["600519"], adjustment="none")
    newer = await repository.reserve_sync(["600519"], adjustment="none")
    original = bar("600519", 4)
    corrected = replace(original, close=original.close + Decimal("0.1"))
    await repository.upsert_fenced([corrected], generations=newer)
    with pytest.raises(PersistenceRecordError, match="superseded"):
        await repository.upsert_fenced([original], generations=older)
    async with pg_factory() as session:
        stored = (await session.execute(select(DailyBarModel))).scalar_one()
        assert stored.close == corrected.close
        assert (
            await session.execute(select(AnalysisStreamModel.source_revision))
        ).scalar_one() == 1


async def test_invalidation_failure_rolls_back_facts_and_qfq_descriptor(pg_factory, monkeypatch):
    repository = SqlAlchemyDailyBarRepository(pg_factory)
    generation = await repository.reserve_sync(["600519"], adjustment="qfq")

    async def fail(*args, **kwargs):
        raise RuntimeError("forced invalidation failure")

    monkeypatch.setattr("app.repositories.market_data.invalidate_changed", fail)
    with pytest.raises(RuntimeError):
        await repository.upsert_fenced(
            [replace(bar("600519"), adjustment=Adjustment.QFQ)],
            generations=generation,
            revisions={"600519": "v1"},
        )
    async with pg_factory() as session:
        assert not (await session.execute(select(DailyBarModel))).scalars().all()
        assert not (await session.execute(select(MarketDataAdjustmentStateModel))).scalars().all()
        assert (
            await session.execute(select(AnalysisStreamModel.source_revision))
        ).scalar_one() == 0


async def test_publisher_lock_blocks_fact_changes_until_commit(pg_factory):
    repository = SqlAlchemyDailyBarRepository(pg_factory)
    generation = await repository.reserve_sync(["600519"], adjustment="none")
    record = bar("600519")
    await repository.upsert_fenced([record], generations=generation)
    async with pg_factory() as publisher:
        await publisher.execute(select(AnalysisStreamModel).with_for_update())
        update_task = asyncio.create_task(
            repository.upsert_fenced(
                [replace(record, high=record.high + 1)], generations=generation
            )
        )
        try:
            await asyncio.sleep(0.1)
            assert not update_task.done()
        finally:
            await publisher.commit()
        await asyncio.wait_for(update_task, 5)
    async with pg_factory() as session:
        assert (
            await session.execute(select(AnalysisStreamModel.source_revision))
        ).scalar_one() == 2
