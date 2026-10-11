"""Targeted coverage for Sprint 4 watch, security and settings contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user, get_security_bootstrap_scheduler
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import (
    DailyBar,
    QuoteSnapshot,
    Security,
    User,
    UserStatus,
)
from app.schemas.state_row import StateRow
from app.services.state_service import state_parameter_key
from tests.snapshot_fixture import current_snapshot
from tests.state_fixture import seed_current_state


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
        session.add(
            QuoteSnapshot(
                security_id=second.id,
                price=Decimal("12.00"),
                change=Decimal("-0.10"),
                change_percent=Decimal("-0.83"),
                timestamp=datetime(2026, 9, 22, 7, 0, tzinfo=UTC),
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
            DailyBar(
                security_id=second.id,
                trade_date=date(2026, 9, 23),
                open=Decimal("12.20"),
                high=Decimal("12.40"),
                low=Decimal("12.10"),
                close=Decimal("12.30"),
                volume=100,
                amount=1230,
                adjust_type="none",
            )
        )
        session.add(
            current_snapshot(
                security_id=first.id,
                trade_date=date(2026, 9, 23),
                indicator_type="RSI",
                parameters={"period": 14},
                values={"value": 71.0},
                previous_values={"value": 69.0},
                delta={"value": 2.0},
            )
        )
        await seed_current_state(
            session,
            StateRow(
                security_id=first.id,
                trade_date=date(2026, 9, 23),
                state_code="RSI_OVERBOUGHT",
                indicator_type="RSI",
                status="ACTIVE",
                parameters={"period": 14},
                parameter_key=state_parameter_key("RSI_OVERBOUGHT", {"period": 14}),
                metadata={"name": "RSI 高位", "level": "WARNING", "active": True},
            ),
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
    client, session_factory, user_one, user_two = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)
    cleared_defaults = await client.put(
        "/api/v1/settings", json={"indicator_settings": {"defaults": []}}
    )
    assert cleared_defaults.status_code == 200

    created = await client.post("/api/v1/watch-tables", json={"name": "核心观察"})
    assert created.status_code == 201
    table = created.json()["data"]
    assert table["stock_count"] == 0
    table_id = table["id"]

    added = await client.post(f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 1})
    assert added.status_code == 201
    assert added.json()["data"]["bootstrap_ready"] is False
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

    async with session_factory() as session:
        security = await session.get(Security, 1)
        assert security is not None
        security.bootstrap_completed_at = datetime.now(UTC) + timedelta(minutes=1)
        await session.commit()

    details = await client.get(f"/api/v1/watch-tables/{table_id}")
    assert details.status_code == 200
    stock = details.json()["data"]["stocks"][0]
    assert stock["security_id"] == 1
    assert stock["bootstrap_ready"] is True
    assert stock["price"]["value"] == 1680.0
    assert details.json()["data"]["adjust_type"] == "qfq"
    assert stock["column_values"][str(column_id)]["value"] == 71.0
    assert "indicators" not in stock and "indicator_values" not in stock and "values" not in stock
    assert stock["states"][0]["state_id"] == "RSI_OVERBOUGHT"

    added_second = await client.post(
        f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 2}
    )
    assert added_second.status_code == 201
    refreshed = await client.get(f"/api/v1/watch-tables/{table_id}")
    second_stock = next(
        item for item in refreshed.json()["data"]["stocks"] if item["security_id"] == 2
    )
    assert second_stock["price"]["value"] == 12.3
    assert second_stock["price"]["change"] is None

    _as_user(app, user_two)
    isolated = await client.get(f"/api/v1/watch-tables/{table_id}")
    assert isolated.status_code == 404
    isolated_column = await client.put(f"/api/v1/columns/{column_id}", json={"visible": True})
    assert isolated_column.status_code == 404


async def test_new_watch_table_uses_product_and_user_indicator_defaults(watch_context) -> None:
    client, _, user_one, user_two = watch_context
    app = client._transport.app  # type: ignore[attr-defined]

    _as_user(app, user_one)
    created = await client.post("/api/v1/watch-tables", json={"name": "产品默认"})
    assert created.status_code == 201
    details = await client.get(f"/api/v1/watch-tables/{created.json()['data']['id']}")
    assert [column["indicator_type"] for column in details.json()["data"]["columns"]] == [
        "MA",
        "RSI",
        "KDJ",
        "BOLL",
        "MACD",
    ]

    _as_user(app, user_two)
    settings = await client.put(
        "/api/v1/settings",
        json={
            "indicator_settings": {
                "defaults": ["RSI", "DIVIDEND_YIELD"],
                "parameters": {"RSI": {"period": 6}},
            }
        },
    )
    assert settings.status_code == 200
    custom = await client.post("/api/v1/watch-tables", json={"name": "用户默认"})
    custom_details = await client.get(f"/api/v1/watch-tables/{custom.json()['data']['id']}")
    columns = custom_details.json()["data"]["columns"]
    assert [column["indicator_type"] for column in columns] == ["RSI", "DIVIDEND_YIELD"]
    assert columns[0]["parameters"] == {"period": 6}


async def test_settings_schema_rejects_invalid_known_display_values(watch_context) -> None:
    client, _, user_one, _ = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    invalid = await client.put("/api/v1/settings", json={"display_settings": {"density": "dense"}})
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    "obsolete",
    [
        {"settings": {"adjust_type": "qfq"}},
        {"notification": {"feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/x"}},
        {"feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/x"},
        {"display_settings": {"old_density": "compact"}},
        {"notification_settings": {"feishu_webhook_configured": True}},
    ],
)
async def test_settings_api_rejects_old_wrappers_and_unknown_fields(
    watch_context, obsolete
) -> None:
    client, _, user, _ = watch_context
    _as_user(client._transport.app, user)  # type: ignore[attr-defined]
    response = await client.put("/api/v1/settings", json=obsolete)
    assert response.status_code == 422


async def test_watch_analysis_mutations_use_injected_bootstrap_scheduler(watch_context) -> None:
    client, _, user_one, _ = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def enqueue(self, symbol: str) -> None:
            self.calls.append(symbol)

    recorder = Recorder()
    app.dependency_overrides[get_security_bootstrap_scheduler] = lambda: recorder
    await client.put("/api/v1/settings", json={"indicator_settings": {"defaults": []}})
    table = await client.post("/api/v1/watch-tables", json={"name": "Bootstrap seam"})
    table_id = table.json()["data"]["id"]
    added = await client.post(f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 1})
    assert added.status_code == 201
    column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "column_type": "INDICATOR",
            "indicator_type": "RSI",
            "parameters": {"period": 6},
            "view_mode": "NUMBER",
        },
    )
    assert column.status_code == 201
    updated = await client.put(
        f"/api/v1/columns/{column.json()['data']['id']}",
        json={"parameters": {"period": 10}},
    )
    assert updated.status_code == 200
    assert recorder.calls == ["600519", "600519", "600519"]


async def test_inactive_security_is_hidden_from_search_and_cannot_be_added(
    watch_context,
) -> None:
    client, session_factory, user_one, _ = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)
    async with session_factory() as session:
        security = await session.get(Security, 2)
        assert security is not None
        security.status = "INACTIVE"
        await session.commit()

    search = await client.get("/api/v1/securities/search", params={"q": "平安"})
    assert search.status_code == 200
    assert search.json()["data"] == []

    created = await client.post("/api/v1/watch-tables", json={"name": "退市过滤"})
    table_id = created.json()["data"]["id"]
    added = await client.post(f"/api/v1/watch-tables/{table_id}/stocks", json={"security_id": 2})
    assert added.status_code == 409
    assert added.json()["error"]["code"] == "SECURITY_INACTIVE"


async def test_settings_and_public_security_contracts(watch_context) -> None:
    client, _, user_one, user_two = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)

    initial = await client.get("/api/v1/settings")
    assert initial.status_code == 200
    assert initial.json()["data"]["settings"] == {
        "notification_settings": {"feishu_webhook_configured": False}
    }
    updated = await client.put("/api/v1/settings", json={"adjust_type": "qfq"})
    assert updated.status_code == 200
    assert updated.json()["data"]["settings"] == {
        "adjust_type": "qfq",
        "notification_settings": {"feishu_webhook_configured": False},
    }

    _as_user(app, user_two)
    other = await client.get("/api/v1/settings")
    assert other.status_code == 200
    assert other.json()["data"]["settings"] == {
        "notification_settings": {"feishu_webhook_configured": False}
    }

    search = await client.get("/api/v1/securities/search", params={"q": "茅台"})
    assert search.status_code == 200
    assert search.json()["data"][0]["id"] == 1
    assert search.json()["data"][0]["security_id"] == 1
    detail = await client.get("/api/v1/securities/600519")
    assert detail.status_code == 200
    quote = await client.get("/api/v1/securities/600519/quote")
    assert quote.status_code == 200
    bars = await client.get("/api/v1/securities/600519/daily-bars", params={"adjust_type": "qfq"})
    assert bars.status_code == 200
    assert len(bars.json()["data"]) == 2


@pytest.mark.asyncio
async def test_column_mode_transition_clears_stale_state_identity(watch_context) -> None:
    client, _session_factory, user_one, _user_two = watch_context
    app = client._transport.app  # type: ignore[attr-defined]
    _as_user(app, user_one)
    await client.put("/api/v1/settings", json={"indicator_settings": {"defaults": []}})
    created = await client.post("/api/v1/watch-tables", json={"name": "指标模式切换"})
    assert created.status_code == 201
    table_id = created.json()["data"]["id"]
    column = await client.post(
        f"/api/v1/watch-tables/{table_id}/columns",
        json={
            "indicator_type": "BOLL",
            "view_mode": "STATUS",
            "state_code": "BOLL_BREAK_UPPER",
            "parameters": {"period": 20, "multiplier": 2},
        },
    )
    assert column.status_code == 201, column.text
    column_id = column.json()["data"]["id"]
    numeric = await client.put(
        f"/api/v1/columns/{column_id}",
        json={
            "view_mode": "NUMBER",
            "state_code": None,
            "parameters": {"period": 20, "multiplier": 2, "field": "upper"},
        },
    )
    assert numeric.status_code == 200, numeric.text
    assert numeric.json()["data"]["state_code"] is None
    assert numeric.json()["data"]["parameters"]["field"] == "upper"
    invalid = await client.put(
        f"/api/v1/columns/{column_id}",
        json={"view_mode": "DELTA", "parameters": {"period": 20, "multiplier": 2}},
    )
    assert invalid.status_code == 400
    composite = await client.put(
        f"/api/v1/columns/{column_id}",
        json={"view_mode": "COMPOSITE", "parameters": {"period": 20, "multiplier": 2}},
    )
    assert composite.status_code == 200, composite.text
    assert composite.json()["data"]["state_code"] is None
