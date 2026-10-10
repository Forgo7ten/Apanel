"""Only a new empty PostgreSQL database is supported by the single Alembic head.

APANEL_TEST_POSTGRES_URL must point to a disposable database. The tests replace
its schema and never run against an application/developer database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import inspect, text

from app.db.base import Base
from tests.test_auth_postgres_concurrency import postgres_engine as postgres_engine

BACKEND = Path(__file__).resolve().parents[1]


async def alembic(*arguments: str) -> tuple[int, str]:
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": os.environ["APANEL_TEST_POSTGRES_URL"],
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *arguments,
        cwd=BACKEND,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 60)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return process.returncode, output.decode()


async def checked(*arguments: str) -> str:
    code, output = await alembic(*arguments)
    assert code == 0, output
    return output


async def test_empty_database_installs_one_canonical_schema_and_calendar(postgres_engine):
    engine = postgres_engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    try:
        heads = await checked("heads")
        assert heads.count("(head)") == 1
        assert "0001_fresh_schema" in heads
        await checked("upgrade", "head")
        await checked("upgrade", "head")  # repeat does not change an empty installation
        await checked("check")
        async with engine.connect() as conn:
            assert (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == "0001_fresh_schema"
            names = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert set(Base.metadata.tables).issubset(names)
            assert "indicator_states" not in names
            assert len(names - {"alembic_version"}) == len(Base.metadata.tables)
            assert (
                await conn.execute(text("SELECT COUNT(*) FROM trading_calendar"))
            ).scalar_one() == 730
            assert (
                await conn.execute(
                    text(
                        "SELECT status FROM trading_calendar "
                        "WHERE market='CN' AND trade_date='2026-02-16'"
                    )
                )
            ).scalar_one() == "CLOSED"
            assert (await conn.execute(text("SELECT COUNT(*) FROM state_events"))).scalar_one() == 0

        code, message = await alembic("downgrade", "base")
        assert code != 0 and "Downgrade is unsupported" in message
        async with engine.connect() as conn:
            assert (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == "0001_fresh_schema"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


async def test_previous_revision_is_not_recognized_or_migrated(postgres_engine):
    engine = postgres_engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('0008_retire_daily_states')")
        )
    try:
        code, message = await alembic("upgrade", "head")
        assert code != 0
        assert "0008_retire_daily_states" in message
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert tables == {"alembic_version"}
            assert (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == "0008_retire_daily_states"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
