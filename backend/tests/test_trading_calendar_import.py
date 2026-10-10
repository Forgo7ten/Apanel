"""Official annual calendar maintenance with no new migration or guessed future dates."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli import main
from app.db.base import Base
from app.models import TradingCalendar
from app.services.trading_calendar_import import (
    import_annual_calendar,
    parse_annual_calendar,
)


def sample_manifest():
    # All exchange URLs are synthetic paths for test validation, never a claim
    # that the exchanges have announced any specific future closure.
    return {
        "year": 2027,
        "market": "CN",
        "sources": {
            "SH": "https://www.sse.com.cn/testing/official-mock",
            "SZ": "https://www.szse.cn/testing/official-mock",
            "BJ": "https://www.bse.cn/testing/official-mock",
        },
        "closed_ranges": [
            ["2027-01-01", "2027-01-03"],
            ["2027-02-08", "2027-02-09"],
        ],
        "operator_confirmed": True,
    }


@pytest.mark.parametrize(
    "modification",
    [
        {"operator_confirmed": False},
        {"market": "SH"},
        {"year": 2024},
        {"closed_ranges": []},
        {"closed_ranges": [["2027-12-31", "2028-01-01"]]},
        {"closed_ranges": [["2027-02-08", "2027-02-10"], ["2027-02-09", "2027-02-12"]]},
        {
            "sources": {
                "SH": "https://evil.example/sse",
                "SZ": "https://www.szse.cn/example",
                "BJ": "https://www.bse.cn/example",
            }
        },
    ],
)
def test_reject_unreviewed_or_inconsistent_calendar_manifest(modification):
    manifest = sample_manifest() | modification
    with pytest.raises(ValueError):
        parse_annual_calendar(manifest)


def test_manifest_does_not_assume_a_new_year_calendar_without_operator_inputs():
    with pytest.raises(ValueError):
        parse_annual_calendar({"year": 2027})
    manifest = sample_manifest()
    parsed = parse_annual_calendar(manifest)
    assert len(parsed.expected_days()) == 365
    assert parsed.expected_days()[date(2027, 1, 1)] is False
    assert parsed.expected_days()[date(2027, 1, 4)] is True
    assert parsed.expected_days()[date(2027, 2, 8)] is False


@pytest.mark.asyncio
async def test_import_complete_year_preserves_actual_conflicts_and_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'calendar.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        calendar = parse_annual_calendar(sample_manifest())
        async with factory() as session:
            session.add(
                TradingCalendar(
                    market="CN",
                    trade_date=date(2027, 2, 8),
                    expected_open=None,
                    actual_open=True,
                    status="OPEN",
                    source="eltdx",
                    source_metadata={},
                )
            )
            await session.commit()
        async with factory() as session:
            counts = await import_annual_calendar(session, calendar)
            assert counts == {"created": 364, "updated": 1, "actual_conflicts": 1}
        async with factory() as session:
            assert (await session.scalar(select(func.count()).select_from(TradingCalendar))) == 365
            conflict = await session.get(TradingCalendar, ("CN", date(2027, 2, 8)))
            assert conflict.actual_open is True
            assert conflict.expected_open is False
            assert conflict.status == "UNKNOWN"
            assert conflict.source_metadata["operator_confirmed"] is True
            assert (await session.get(TradingCalendar, ("CN", date(2027, 1, 4)))).status == "OPEN"
            assert (await session.get(TradingCalendar, ("CN", date(2027, 1, 2)))).status == "CLOSED"
            assert await import_annual_calendar(session, calendar) == {
                "created": 0,
                "updated": 0,
                "actual_conflicts": 1,
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_conflicting_official_revision_needs_explicit_replace_and_rolls_back(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'revision.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        calendar = parse_annual_calendar(sample_manifest())
        async with factory() as session:
            await import_annual_calendar(session, calendar)
        revised = sample_manifest()
        revised["closed_ranges"] = [["2027-01-01", "2027-01-03"], ["2027-02-08", "2027-02-10"]]
        new_calendar = parse_annual_calendar(revised)
        async with factory() as session:
            with pytest.raises(ValueError, match="--replace-existing"):
                await import_annual_calendar(session, new_calendar)
            await session.rollback()
        async with factory() as session:
            untouched = await session.get(TradingCalendar, ("CN", date(2027, 2, 10)))
            assert untouched.status == "OPEN"
            updated = await import_annual_calendar(session, new_calendar, replace_existing=True)
            assert updated["updated"] >= 1
        async with factory() as session:
            assert (
                await session.get(TradingCalendar, ("CN", date(2027, 2, 10)))
            ).status == "CLOSED"
    finally:
        await engine.dispose()


def test_cli_preview_is_read_only_and_refuses_unconfirmed_manifest(tmp_path, capsys):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(sample_manifest()), encoding="utf-8")
    result = main(["import-trading-calendar", "--file", str(path)], settings=SimpleNamespace())
    assert result == 0
    assert "Preview only" in capsys.readouterr().out
    manifest = sample_manifest()
    manifest["operator_confirmed"] = False
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["import-trading-calendar", "--file", str(path)], settings=SimpleNamespace()) == 1


def test_cli_apply_writes_calendar_to_the_current_database_without_schema_changes(tmp_path, capsys):
    import asyncio

    from app.core.config import Settings

    url = f"sqlite+aiosqlite:///{tmp_path / 'cli-calendar.db'}"
    engine = create_async_engine(url)

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    asyncio.run(engine.dispose())
    path = tmp_path / "official.json"
    path.write_text(json.dumps(sample_manifest()), encoding="utf-8")
    settings = Settings(
        app_env="test",
        database_url=url,
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
    )
    assert main(["import-trading-calendar", "--file", str(path), "--apply"], settings=settings) == 0
    assert "created=365" in capsys.readouterr().out

    async def assert_present():
        reader = create_async_engine(url)
        try:
            factory = async_sessionmaker(reader)
            async with factory() as session:
                assert (
                    await session.scalar(select(func.count()).select_from(TradingCalendar))
                ) == 365
        finally:
            await reader.dispose()

    asyncio.run(assert_present())
