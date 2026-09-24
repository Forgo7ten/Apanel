"""Targeted coverage for Sprint 4 watch, security and settings contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
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


@pytest_asyncio.fixture
async def watch_context(
    tmp_path,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], User, User]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'watch.db'}"
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
            username="watch-one",
            email="watch-one@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        user_two = User(
            username="watch-two",
            email="watch-two@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        first = Security(
            symbol="600519",
            name="贵州茅台",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        second = Security(
            symbol="000001",
            name="平安银行",
            market="SZ",
            exchange="SZ",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add_all([user_one, user_two, first, second])
        await session.flush()
        now = datetime.now(UTC)
        session.add(
            QuoteSnapshot(
                security_id=first.id,
                price=Decimal("1680.00"),
                change=Decimal("1.20"),
                change_percent=Decimal("0.07"),
                timestamp=now,
            )
        )
        for offset in range(2):
            trade_date = date(2026, 9, 22) + timedelta(days=offset)
            close = Decimal(str(100 + offset))
            session.add(
                DailyBar(
                    security_id=first.id,
                    trade_date=trade_date,
                    open=close,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=100,
                    amount=10000,
                    adjust_type="qfq",
                )
            )
        session.add(
            IndicatorSnapshot(
                security_id=first.id,
                trade_date=date(2026, 9, 23),
                indicator_type="RSI",
                parameters={"period": 14},
                values={"value": 71.0},
                previous_values={"value": 69.0},
                delta={"value": 2.0},
            )
        )
        session.add(
            IndicatorState(
                security_id=first.id,
                trade_date=date(2026, 9, 23),
                state_code="RSI_OVERBOUGHT",
                indicator_type="RSI",
                status="ACTIVE",
                metadata={"name": "RSI 高位", "level": "WARNING", "active": True},
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


async def test_watch_table_endpoints_aggregate_data_and_enforce_ownership(watch_context) -> None:
    client, _, user_one, user_two = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    created = await client.post("/api/v1/watch-tables", json={"name": "核心观察"})
    assert created.status_code == 201
    table = created.json()["data"]
    assert table["stock_count"] == 0
    table_id = table["id"]

    added = await client.post(
        f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 1}
    )
    assert added.status_code == 201
    duplicate = await client.post(
        f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 1}
    )
    assert duplicate.status_code == 409
    missing = await client.post(
        f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 999}
    )
    assert missing.status_code == 404

    column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "RSI",
            "parameters": {"period": 14},
            "view_mode": "NUMBER",
        },
    )
    assert column.status_code == 201
    column_id = column.json()["data"]["id"]
    duplicate_column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "RSI",
            "parameters": {"period": 14},
            "view_mode": "DELTA",
        },
    )
    assert duplicate_column.status_code == 409
    updated = await client.put(
        f"/api/v1/columns/{column_id}",
        json={"hidden": True, "width": 180, "order": 0},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["visible"] is False
    assert updated.json()["data"]["width"] == 180

    details = await client.get(f"/api/v1/watch-tables/{table_id}")
    assert details.status_code == 200
    stock = details.json()["data"]["stocks"][0]
    assert stock["security_id"] == 1
    assert stock["price"]["value"] == 1680.0
    assert stock["indicators"]["RSI"]["value"] == 71.0
    assert stock["states"][0]["state_id"] == "RSI_OVERBOUGHT"

    _as_user(app, user_two)
    isolated = await client.get(f"/api/v1/watch-tables/{table_id}")
    assert isolated.status_code == 404
    isolated_column = await client.put(
        f"/api/v1/columns/{column_id}", json={"visible": True}
    )
    assert isolated_column.status_code == 404


async def test_settings_and_public_security_contracts(watch_context) -> None:
    client, _, user_one, user_two = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    initial = await client.get("/api/v1/settings")
    assert initial.status_code == 200
    assert initial.json()["data"]["settings"] == {}
    updated = await client.put("/api/v1/settings", json={"adjust_type": "qfq"})
    assert updated.status_code == 200
    assert updated.json()["data"]["settings"] == {"adjust_type": "qfq"}

    _as_user(app, user_two)
    other = await client.get("/api/v1/settings")
    assert other.status_code == 200
    assert other.json()["data"]["settings"] == {}

    search = await client.get("/api/v1/securities/search", params={"q": "茅台"})
    assert search.status_code == 200
    assert search.json()["data"][0]["id"] == 1
    assert search.json()["data"][0]["security_id"] == 1
    detail = await client.get("/api/v1/securities/600519")
    assert detail.status_code == 200
    quote = await client.get("/api/v1/securities/600519/quote")
    assert quote.status_code == 200
    bars = await client.get(
        "/api/v1/securities/600519/daily-bars", params={"adjust_type": "qfq"}
    )
    assert bars.status_code == 200
    assert len(bars.json()["data"]) == 2
