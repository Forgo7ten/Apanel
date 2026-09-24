"""TTM dividend-yield repository, service, API and watch-table coverage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import DailyBar, QuoteSnapshot, Security, User, UserStatus
from app.repositories.security import DIVIDEND_EVENTS_TABLE, DividendEventRecord, SecurityRepository
from app.services.dividend_service import calculate_ttm_dividend_yield


@pytest_asyncio.fixture
async def dividend_context(
    tmp_path,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], User]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'dividend.db'}"
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
        user = User(
            username="dividend-user",
            email="dividend-user@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        quoted = Security(
            symbol="600519",
            name="贵州茅台",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        fallback = Security(
            symbol="000001",
            name="平安银行",
            market="SZ",
            exchange="SZ",
            security_type="STOCK",
            status="ACTIVE",
        )
        no_price = Security(
            symbol="300001",
            name="无行情证券",
            market="SZ",
            exchange="SZ",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add_all([user, quoted, fallback, no_price])
        await session.flush()

        quote_time = datetime(2026, 9, 23, 10, 30, tzinfo=UTC)
        session.add(
            QuoteSnapshot(
                security_id=quoted.id,
                price=Decimal("50.00"),
                change=Decimal("1.00"),
                change_percent=Decimal("2.00"),
                timestamp=quote_time,
            )
        )
        session.add(
            DailyBar(
                security_id=fallback.id,
                trade_date=date(2026, 9, 22),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("40.00"),
                volume=Decimal("100"),
                amount=Decimal("4000"),
                adjust_type="none",
            )
        )
        await session.flush()
        await session.execute(
            insert(DIVIDEND_EVENTS_TABLE),
            [
                {
                    "security_id": quoted.id,
                    "date": date(2025, 9, 23),
                    "cash_amount": Decimal("1.10"),
                },
                {
                    "security_id": quoted.id,
                    "date": date(2025, 9, 22),
                    "cash_amount": Decimal("9.00"),
                },
                {
                    "security_id": quoted.id,
                    "date": date(2026, 9, 23),
                    "cash_amount": Decimal("0.90"),
                },
                {
                    "security_id": fallback.id,
                    "date": date(2026, 6, 30),
                    "cash_amount": Decimal("2.00"),
                },
            ],
        )
        await session.commit()
        await session.refresh(user)

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
        yield client, session_factory, user
    app.dependency_overrides.clear()
    await engine.dispose()


def _as_user(app, user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def test_ttm_calculation_uses_inclusive_calendar_boundaries_and_decimal() -> None:
    result = calculate_ttm_dividend_yield(
        [
            DividendEventRecord(1, 1, date(2025, 9, 23), Decimal("1.10")),
            DividendEventRecord(2, 1, date(2025, 9, 22), Decimal("9.00")),
            DividendEventRecord(3, 1, date(2026, 9, 23), Decimal("0.90")),
        ],
        price=Decimal("50.00"),
        as_of=datetime(2026, 9, 23, 10, 30, tzinfo=UTC),
        price_source="QUOTE",
    )

    assert result.dividend_total == Decimal("2.00")
    assert result.dividend_yield == Decimal("0.04")
    assert result.window_start == date(2025, 9, 23)
    assert result.window_end == date(2026, 9, 23)
    assert result.dividend_event_count == 2


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_ttm_calculation_rejects_non_positive_price(price: Decimal) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        calculate_ttm_dividend_yield(
            [],
            price=price,
            as_of=date(2026, 9, 23),
            price_source="QUOTE",
        )


@pytest.mark.asyncio
async def test_repository_returns_dividend_events_with_date_filter(dividend_context) -> None:
    _, session_factory, user = dividend_context
    async with session_factory() as session:
        security = await session.get(Security, 1)
        assert security is not None
        events = await SecurityRepository(session).dividend_events(
            security.id,
            start_date=date(2025, 9, 23),
            end_date=date(2026, 9, 23),
        )

    assert [event.cash_amount for event in events] == [Decimal("1.10"), Decimal("0.90")]


async def test_dividend_yield_api_prefers_quote_and_returns_explanation(dividend_context) -> None:
    client, _, _ = dividend_context
    response = await client.get("/api/v1/securities/600519/dividend-yield")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dividend_total"] == 2.0
    assert data["price"] == 50.0
    assert data["yield"] == 0.04
    assert data["price_source"] == "QUOTE"
    assert data["as_of"] == "2026-09-23T10:30:00Z"
    assert data["window_start"] == "2025-09-23"


async def test_dividend_yield_api_falls_back_to_latest_daily_close(dividend_context) -> None:
    client, _, _ = dividend_context
    response = await client.get("/api/v1/securities/000001/dividend-yield")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["price"] == 40.0
    assert data["yield"] == 0.05
    assert data["price_source"] == "DAILY_BAR_CLOSE"
    assert data["as_of"] == "2026-09-22T00:00:00Z"


async def test_dividend_yield_api_reports_missing_price(dividend_context) -> None:
    client, _, _ = dividend_context
    response = await client.get("/api/v1/securities/300001/dividend-yield")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PRICE_NOT_FOUND"


async def test_watch_detail_projects_dividend_yield_column(dividend_context) -> None:
    client, _, user = dividend_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user)

    created = await client.post("/api/v1/watch-tables", json={"name": "高股息"})
    table_id = created.json()["data"]["id"]
    await client.post(f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 1})
    column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "DIVIDEND_YIELD",
            "view_mode": "NUMBER",
        },
    )
    assert column.status_code == 201

    details = await client.get(f"/api/v1/watch-tables/{table_id}")

    assert details.status_code == 200
    data = details.json()["data"]["stocks"][0]["indicators"]["DIVIDEND_YIELD"]
    assert data["value"] == 0.04
    assert data["yield"] == 0.04
    assert data["dividend_total"] == 2.0
    assert data["price"] == 50.0
    assert data["price_source"] == "QUOTE"
