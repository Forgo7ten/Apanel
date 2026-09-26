"""Offline Tab1 coverage for parameter-aware watch-table projections."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import (
    DailyBar,
    IndicatorSnapshot,
    IndicatorState,
    QuoteSnapshot,
    Security,
    User,
    UserStatus,
)
from app.repositories.security import DIVIDEND_EVENTS_TABLE


@pytest_asyncio.fixture
async def tab1_context(
    tmp_path,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], User, User]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tab1-parameters.db'}"
    settings = Settings(
        app_env="test",
        database_url=database_url,
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=False,
    )
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user_one = User(
            username="tab1-one",
            email="tab1-one@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        user_two = User(
            username="tab1-two",
            email="tab1-two@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        security = Security(
            symbol="600519",
            name="贵州茅台",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add_all([user_one, user_two, security])
        await session.flush()

        session.add(
            QuoteSnapshot(
                security_id=security.id,
                price=Decimal("1680.00"),
                change=Decimal("1.20"),
                change_percent=Decimal("0.07"),
                timestamp=datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
            )
        )
        session.add(
            DailyBar(
                security_id=security.id,
                trade_date=date(2026, 9, 23),
                open=Decimal("1670"),
                high=Decimal("1690"),
                low=Decimal("1660"),
                close=Decimal("1680"),
                volume=100,
                amount=10000,
                adjust_type="qfq",
            )
        )
        session.add_all(
            [
                IndicatorSnapshot(
                    security_id=security.id,
                    trade_date=date(2026, 9, 23),
                    indicator_type="MA",
                    parameters={"periods": [5, 20]},
                    values={"MA5": 100.0, "MA20": 120.0},
                    previous_values={"MA5": 98.0, "MA20": 117.0},
                    delta={"MA5": 2.0, "MA20": 3.0},
                ),
                IndicatorSnapshot(
                    security_id=security.id,
                    trade_date=date(2026, 9, 23),
                    indicator_type="BOLL",
                    parameters={"period": 20, "multiplier": 2.0},
                    values={"upper": 110.0, "middle": 100.0, "lower": 90.0, "width": 0.2},
                    previous_values={
                        "upper": 109.0,
                        "middle": 99.0,
                        "lower": 89.0,
                        "width": 0.21,
                    },
                    delta={"upper": 1.0, "middle": 1.0, "lower": 1.0, "width": -0.01},
                ),
                IndicatorSnapshot(
                    security_id=security.id,
                    trade_date=date(2026, 9, 23),
                    indicator_type="RSI",
                    parameters={"period": 14},
                    values={"value": 71.0},
                    previous_values={"value": 69.0},
                    delta={"value": 2.0},
                ),
            ]
        )
        session.add(
            IndicatorState(
                security_id=security.id,
                trade_date=date(2026, 9, 23),
                state_code="BOLL_WIDTH_NARROWING",
                indicator_type="BOLL",
                status="ACTIVE",
                metadata={"name": "带口收窄", "level": "WARNING", "active": True},
            )
        )
        await session.flush()
        await session.execute(
            insert(DIVIDEND_EVENTS_TABLE).values(
                security_id=security.id,
                date=date(2026, 6, 1),
                cash_amount=Decimal("10.00"),
            )
        )
        await session.commit()
        await session.refresh(user_one)
        await session.refresh(user_two)

    app = create_app(settings)
    app.state.db_session_factory = session_factory
    app.state.db_engine = engine

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, session_factory, user_one, user_two
    app.dependency_overrides.clear()
    await engine.dispose()


def _as_user(app, user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


async def test_tab1_detail_matches_snapshot_parameters_and_projects_values(tab1_context) -> None:
    client, _, user_one, _ = tab1_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    created = await client.post("/api/v1/watch-tables", json={"name": "参数观察"})
    assert created.status_code == 201
    table_id = created.json()["data"]["id"]

    added = await client.post(
        f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 1}
    )
    assert added.status_code == 201

    ma_column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "MA",
            "parameters": {"period": 20},
            "view_mode": "DELTA",
        },
    )
    assert ma_column.status_code == 201

    boll_column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "BOLL",
            "parameters": {"period": 20, "multiplier": 2},
            "view_mode": "COMPOSITE",
        },
    )
    assert boll_column.status_code == 201

    # This column has the right type but the wrong parameters. It must not
    # receive the latest RSI snapshot merely because one exists.
    bad_column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "RSI",
            "parameters": {"period": 6},
            "view_mode": "NUMBER",
        },
    )
    assert bad_column.status_code == 201

    details = await client.get(f"/api/v1/watch-tables/{table_id}")
    assert details.status_code == 200
    stock = details.json()["data"]["stocks"][0]

    assert stock["indicators"]["MA"]["value"] == 120.0
    assert stock["indicators"]["MA"]["current_value"] == 120.0
    assert stock["indicators"]["MA"]["delta"] == 3.0
    assert stock["indicators"]["BOLL"]["upper"] == 110.0
    assert stock["indicators"]["BOLL"]["current_value"]["upper"] == 110.0
    assert stock["indicators"]["BOLL"]["delta"]["width"] == -0.01
    assert stock["column_values"][str(ma_column.json()["data"]["id"])] == {
        "column_id": ma_column.json()["data"]["id"],
        "view_mode": "DELTA",
        "indicator_type": "MA",
        "parameters": {"period": 20},
        "available": True,
        "value": 120.0,
        "previous_value": 117.0,
        "delta": 3.0,
        "direction": "UP",
    }
    boll_column_value = stock["column_values"][str(boll_column.json()["data"]["id"])]
    assert boll_column_value["view_mode"] == "COMPOSITE"
    assert boll_column_value["available"] is True
    assert boll_column_value["fields"]["width"] == {
        "value": 0.2,
        "previous_value": 0.21,
        "delta": -0.01,
        "direction": "DOWN",
    }
    assert stock["states"][0]["state_id"] == "BOLL_WIDTH_NARROWING"
    assert "RSI" not in stock["indicators"]


async def test_composite_number_and_delta_columns_require_a_field_selector(tab1_context) -> None:
    client, _, user_one, _ = tab1_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    created = await client.post("/api/v1/watch-tables", json={"name": "复合选择器"})
    table_id = created.json()["data"]["id"]
    invalid = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "BOLL",
            "parameters": {"period": 20, "multiplier": 2},
            "view_mode": "NUMBER",
        },
    )

    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INDICATOR_FIELD_REQUIRED"


async def test_tab1_detail_isolated_and_bad_parameters_are_rejected(tab1_context) -> None:
    client, _, user_one, user_two = tab1_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    created = await client.post("/api/v1/watch-tables", json={"name": "参数校验"})
    table_id = created.json()["data"]["id"]
    bad = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "MA",
            "parameters": {"period": 0},
            "view_mode": "NUMBER",
        },
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_INDICATOR_PARAMETERS"

    _as_user(app, user_two)
    isolated = await client.get(f"/api/v1/watch-tables/{table_id}")
    assert isolated.status_code == 404
