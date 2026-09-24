"""Targeted persistence and API coverage for Sprint 3/4."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import (
    DailyBar,
    IndicatorSnapshot,
    IndicatorState,
    Security,
    StateDefinition,
)
from app.services.indicator_service import IndicatorService, serialize_current_indicators
from app.services.state_service import StateService


@pytest_asyncio.fixture
async def indicator_context(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'indicator-state.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        security = Security(
            symbol="600519",
            name="示例股票",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add(security)
        await session.flush()
        for index in range(40):
            close = Decimal(str(100 + index * 0.2))
            session.add(
                DailyBar(
                    security_id=security.id,
                    trade_date=date(2026, 1, 1) + timedelta(days=index),
                    open=close,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=100,
                    amount=10000,
                    adjust_type="none",
                )
            )
        await session.commit()
    yield session_factory
    await engine.dispose()


async def test_indicator_calculation_persists_defaults_and_is_idempotent(indicator_context) -> None:
    async with indicator_context() as session:
        first = await IndicatorService(session).calculate("600519")
        first_count = (
            await session.execute(select(func.count()).select_from(IndicatorSnapshot))
        ).scalar_one()
        second = await IndicatorService(session).calculate("600519")
        second_count = (
            await session.execute(select(func.count()).select_from(IndicatorSnapshot))
        ).scalar_one()

        latest = max(first, key=lambda item: item.trade_date)
        assert {item.indicator_type for item in first if item.trade_date == latest.trade_date} == {
            "MA",
            "PROJECTED_MA",
            "RSI",
            "KDJ",
            "BOLL",
            "MACD",
        }
        assert latest.values
        assert second_count == first_count
        assert len(second) == len(first)


async def test_state_history_survives_a_new_service_instance(indicator_context) -> None:
    async with indicator_context() as session:
        first = await StateService(session).calculate("600519")
        definition_count = (
            await session.execute(select(func.count()).select_from(StateDefinition))
        ).scalar_one()
        first_count = (
            await session.execute(select(func.count()).select_from(IndicatorState))
        ).scalar_one()
        second = await StateService(session).calculate("600519")
        second_count = (
            await session.execute(select(func.count()).select_from(IndicatorState))
        ).scalar_one()

        assert definition_count == 18
        assert first_count == second_count
        assert len(first) == len(second)
        assert any(item.status == "ACTIVE" for item in second)


async def test_indicator_and_state_routes_return_current_and_history(indicator_context) -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=False,
    )
    # The app's settings URL is not used after replacing its request session
    # dependency with the fixture's already-open SQLite database.
    app = create_app(settings)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with indicator_context() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            indicators = await client.get("/api/v1/securities/600519/indicators")
            indicator_history = await client.get(
                "/api/v1/securities/600519/indicators/history"
            )
            states = await client.get("/api/v1/securities/600519/states")
            state_history = await client.get("/api/v1/securities/600519/states/history")
    finally:
        app.dependency_overrides.clear()

    assert indicators.status_code == 200
    assert indicators.json()["data"]["MA"]["MA5"]
    assert indicator_history.status_code == 200
    assert indicator_history.json()["data"]["items"]
    assert states.status_code == 200
    assert state_history.status_code == 200
    assert state_history.json()["data"]["items"]


def test_indicator_projection_has_stable_delta_for_missing_and_present_values() -> None:
    missing = IndicatorSnapshot(
        security_id=1,
        trade_date=date(2026, 9, 24),
        indicator_type="RSI",
        parameters={"period": 14},
        values={"value": 70},
        previous_values=None,
        delta=None,
    )
    present = IndicatorSnapshot(
        security_id=1,
        trade_date=date(2026, 9, 24),
        indicator_type="RSI",
        parameters={"period": 14},
        values={"value": 71},
        previous_values={"value": 70},
        delta={"value": 1},
    )

    data = serialize_current_indicators([missing, present])

    assert data["RSI"]["delta"] == 1.0

    missing_only = serialize_current_indicators([missing])
    assert missing_only["RSI"]["delta"] is None
