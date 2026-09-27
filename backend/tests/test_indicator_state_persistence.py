"""Targeted persistence and API coverage for Sprint 3/4."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import delete, func, select
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
    TradingCalendar,
)
from app.services.indicator_service import IndicatorService
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
        calendar_day = date(2026, 1, 1)
        while calendar_day <= date(2026, 2, 28):
            session.add(
                TradingCalendar(
                    market="CN",
                    trade_date=calendar_day,
                    expected_open=True,
                    actual_open=True,
                    status="OPEN",
                    source="TEST",
                    source_metadata={},
                )
            )
            calendar_day += timedelta(days=1)
        for index in range(40):
            close = Decimal(str(100 + index * 0.2))
            for adjustment, offset in (("qfq", Decimal("0")), ("none", Decimal("5"))):
                selected_close = close + offset
                session.add(
                    DailyBar(
                        security_id=security.id,
                        trade_date=date(2026, 1, 1) + timedelta(days=index),
                        open=selected_close,
                        high=selected_close + 1,
                        low=selected_close - 1,
                        close=selected_close,
                        volume=100,
                        amount=10000,
                        adjust_type=adjustment,
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
        await IndicatorService(session).calculate("600519")
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


async def test_state_calculation_marks_missing_previous_trading_day_unknown(
    indicator_context,
) -> None:
    async with indicator_context() as session:
        await IndicatorService(session).calculate("600519")
        missing_date = date(2026, 1, 20)
        affected_date = date(2026, 1, 21)
        await session.execute(
            delete(IndicatorSnapshot).where(IndicatorSnapshot.trade_date == missing_date)
        )
        await session.commit()

        await StateService(session).calculate("600519")
        affected = list(
            (
                await session.execute(
                    select(IndicatorState).where(IndicatorState.trade_date == affected_date)
                )
            ).scalars()
        )

        assert affected
        assert {row.status for row in affected} == {"UNKNOWN"}
        assert {row.metadata["reason"] for row in affected} == {
            "missing_previous_trading_day"
        }
        assert {row.metadata["expected_previous_trade_date"] for row in affected} == {
            missing_date.isoformat()
        }


async def test_indicator_and_state_routes_return_current_and_history(indicator_context) -> None:
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=False,
    )
    # Read routes are side-effect free; materialize both adjustment series first.
    async with indicator_context() as session:
        for adjustment in ("qfq", "none"):
            await IndicatorService(session).calculate("600519", adjustment=adjustment)
            await StateService(session).calculate("600519", adjustment=adjustment)
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
            indicator_history = await client.get("/api/v1/securities/600519/indicators/history")
            states = await client.get("/api/v1/securities/600519/states")
            state_history = await client.get("/api/v1/securities/600519/states/history")
            none_results = {
                path: await client.get(path, params={"adjust": "none"})
                for path in (
                    "/api/v1/securities/600519/indicators",
                    "/api/v1/securities/600519/indicators/history",
                    "/api/v1/securities/600519/states",
                    "/api/v1/securities/600519/states/history",
                )
            }
    finally:
        app.dependency_overrides.clear()

    assert indicators.status_code == 200
    assert indicators.json()["data"]["MA"]["MA5"]
    assert indicator_history.status_code == 200
    assert indicator_history.json()["data"]["items"]
    assert states.status_code == 200
    assert state_history.status_code == 200
    assert state_history.json()["data"]["items"]
    assert all(response.status_code == 200 for response in none_results.values())
    assert (
        none_results["/api/v1/securities/600519/indicators/history"].json()["data"]["items"][0][
            "adjust_type"
        ]
        == "none"
    )


async def test_history_services_only_read_persisted_rows(indicator_context) -> None:
    async with indicator_context() as session:
        indicator_service = IndicatorService(session)
        await indicator_service.calculate("600519")
        state_service = StateService(session)
        await state_service.calculate("600519")

        async def unexpected_indicator_calculation(*_args, **_kwargs):
            raise AssertionError("history must not calculate indicators")

        async def unexpected_state_calculation(*_args, **_kwargs):
            raise AssertionError("history must not calculate states")

        indicator_service.calculate = unexpected_indicator_calculation
        state_service.calculate = unexpected_state_calculation

        indicator_rows = await indicator_service.history(
            "600519", start=date(2026, 1, 10), end=date(2026, 1, 12)
        )
        state_rows = await state_service.history(
            "600519", start=date(2026, 1, 10), end=date(2026, 1, 12)
        )

        assert indicator_rows
        assert state_rows


async def test_history_supports_qfq_and_none_without_fallback(indicator_context) -> None:
    async with indicator_context() as session:
        service = IndicatorService(session)
        state_service = StateService(session)
        for adjustment in ("qfq", "none"):
            await service.calculate("600519", adjustment=adjustment)
            await state_service.calculate("600519", adjustment=adjustment)
        qfq = await service.history("600519", adjustment="qfq")
        none = await service.history("600519", adjustment="none")
        assert qfq and none
        assert all(row.adjust_type == "qfq" for row in qfq)
        assert all(row.adjust_type == "none" for row in none)
        assert await state_service.history("600519", adjustment="none")


async def test_indicator_and_state_persistence_keep_adjustments_separate(indicator_context) -> None:
    async with indicator_context() as session:
        indicator_service = IndicatorService(session)
        state_service = StateService(session)
        await indicator_service.calculate("600519", adjustment="qfq")
        await state_service.calculate("600519", adjustment="qfq")
        qfq_snapshot_count = (
            await session.execute(
                select(func.count())
                .select_from(IndicatorSnapshot)
                .where(IndicatorSnapshot.adjust_type == "qfq")
            )
        ).scalar_one()
        await indicator_service.calculate("600519", adjustment="none")
        await state_service.calculate("600519", adjustment="none")
        none_snapshot_count = (
            await session.execute(
                select(func.count())
                .select_from(IndicatorSnapshot)
                .where(IndicatorSnapshot.adjust_type == "none")
            )
        ).scalar_one()
        assert qfq_snapshot_count > 0
        assert none_snapshot_count > 0

async def test_alert_state_observation_ignores_unknown_state() -> None:
    from types import SimpleNamespace

    from app.services.alert_observation_service import AlertObservationService

    class Repository:
        async def latest_states(self, *_args, **_kwargs):
            return [
                SimpleNamespace(
                    status="UNKNOWN",
                    trade_date=date(2026, 1, 21),
                    state_code="MA_CROSS_UP",
                    parameter_key="v1_test",
                )
            ]

    service = AlertObservationService(SimpleNamespace())
    service.alert_repository = Repository()
    rule = SimpleNamespace(
        security_id=1,
        adjust_type="qfq",
        state_code="MA_CROSS_UP",
        parameter_key="v1_test",
    )

    assert await service.resolve_state(rule) is None
