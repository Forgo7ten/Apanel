"""Persistence, API and delivery coverage for Sprint 5 alerts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import get_current_user
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AlertInstance,
    AlertRule,
    IndicatorSnapshot,
    IndicatorState,
    Notification,
    Security,
    User,
    UserSetting,
    UserStatus,
)
from app.providers.notification import FeishuWebhookError, NotificationProviderRegistry
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import AlertService
from app.services.settings_service import UserSettingsService


class FakeProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def send(self, _user, _message) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


@pytest_asyncio.fixture
async def alert_context(tmp_path) -> AsyncIterator[dict[str, object]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'alerts.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user_one = User(
            username="alerts-one",
            email="alerts-one@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        user_two = User(
            username="alerts-two",
            email="alerts-two@example.com",
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
            IndicatorSnapshot(
                security_id=security.id,
                trade_date=date(2026, 9, 24),
                indicator_type="RSI",
                parameters={"period": 14},
                values={"value": 71.0},
                previous_values={"value": 69.0},
                delta={"value": 2.0},
            )
        )
        session.add(
            IndicatorState(
                security_id=security.id,
                trade_date=date(2026, 9, 24),
                state_code="BOLL_WIDTH_NARROWING",
                indicator_type="BOLL",
                status="ACTIVE",
                metadata={"name": "带口收窄", "active": True},
            )
        )
        await session.commit()
        await session.refresh(user_one)
        await session.refresh(user_two)
        await session.refresh(security)

    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=False,
    )
    app = create_app(settings)
    current_user = {"value": user_one}

    async def override_current_user() -> User:
        return current_user["value"]

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_db] = override_get_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield {
            "client": client,
            "app": app,
            "session_factory": session_factory,
            "user_one": user_one,
            "user_two": user_two,
            "security": security,
            "current_user": current_user,
        }
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_alert_crud_and_user_isolation(alert_context) -> None:
    client = alert_context["client"]
    created = await client.post(
        "/api/v1/alerts",
        json={
            "security_id": alert_context["security"].id,
            "condition_type": "STATE",
            "state_id": "BOLL_WIDTH_NARROWING",
        },
    )
    assert created.status_code == 201
    rule = created.json()["data"]
    assert rule["state_id"] == rule["state_code"] == "BOLL_WIDTH_NARROWING"
    assert rule["symbol"] == "600519"

    unknown = await client.post(
        "/api/v1/alerts",
        json={
            "security_id": alert_context["security"].id,
            "condition_type": "STATE",
            "state_code": "UNKNOWN_STATE",
        },
    )
    assert unknown.status_code == 400
    unknown_update = await client.put(
        f"/api/v1/alerts/{rule['id']}",
        json={"state_code": "UNKNOWN_STATE"},
    )
    assert unknown_update.status_code == 400

    alert_context["current_user"]["value"] = alert_context["user_two"]
    assert (await client.get("/api/v1/alerts")).json()["data"] == []
    assert (
        await client.put(f"/api/v1/alerts/{rule['id']}", json={"enabled": False})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/alerts/{rule['id']}")).status_code == 404

    alert_context["current_user"]["value"] = alert_context["user_one"]
    updated = await client.put(f"/api/v1/alerts/{rule['id']}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["data"]["enabled"] is False
    assert (await client.delete(f"/api/v1/alerts/{rule['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_value_edge_trigger_survives_service_restart_and_reset(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    provider = FakeProvider()
    async with session_factory() as session:
        service = AlertService(session, notification_provider=provider)
        created = await service.create(
            user.id,
            AlertRuleCreateRequest(
                security_id=security.id,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        first = await service.evaluate_user(user.id)
        assert first[0].triggered is True
        held = await AlertService(session, notification_provider=provider).evaluate_user(user.id)
        assert held[0].triggered is False
        snapshot = (await session.execute(select(IndicatorSnapshot))).scalar_one()
        snapshot.values = {"value": 60.0}
        snapshot.previous_values = {"value": 71.0}
        snapshot.delta = {"value": -11.0}
        await session.commit()
        reset = await AlertService(session, notification_provider=provider).evaluate_user(user.id)
        assert reset[0].status == "RESET"
        snapshot.values = {"value": 72.0}
        snapshot.previous_values = {"value": 60.0}
        snapshot.delta = {"value": 12.0}
        await session.commit()
        reentered = await AlertService(
            session, notification_provider=provider
        ).evaluate_user(user.id)
        assert reentered[0].triggered is True
        assert provider.calls == 2
        instance = (await session.execute(select(AlertInstance))).scalar_one()
        assert instance.status == "ACTIVE"
        notifications = list((await session.execute(select(Notification))).scalars())
        assert len(notifications) == 2
        assert all(item.status == "SENT" for item in notifications)
        assert created.id == 1


@pytest.mark.asyncio
async def test_state_trigger_and_provider_failure_are_persisted(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        await AlertService(session).create(
            user.id,
            AlertRuleCreateRequest(
                security_id=security.id,
                condition_type="STATE",
                state_id="BOLL_WIDTH_NARROWING",
            ),
        )
        provider = FakeProvider(FeishuWebhookError("request failed"))
        result = await AlertService(session, notification_provider=provider).evaluate_user(user.id)
        assert result[0].triggered is True
        notification = (await session.execute(select(Notification))).scalar_one()
        assert notification.status == "FAILED"
        assert notification.error_code == "FEISHU_ERROR"
        assert "webhook" not in (notification.error_message or "").lower()
        held = await AlertService(session, notification_provider=provider).evaluate_user(user.id)
        assert held[0].triggered is False


@pytest.mark.asyncio
async def test_notification_destination_failure_is_persisted_as_failed(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        await AlertService(session).create(
            user.id,
            AlertRuleCreateRequest(
                security_id=security.id,
                condition_type="STATE",
                state_id="BOLL_WIDTH_NARROWING",
            ),
        )
        service = AlertService(session)

        async def fail_destination(_user_id: int) -> str | None:
            raise RuntimeError("destination lookup failed")

        service.notification_service._user_webhook = fail_destination
        result = await service.evaluate_user(user.id)
        notification = (await session.execute(select(Notification))).scalar_one()

        assert result[0].triggered is True
        assert notification.status == "FAILED"
        assert notification.error_code == "PROVIDER_ERROR"
        assert notification.error_message == "Notification provider failed."


@pytest.mark.asyncio
async def test_failed_notification_can_be_retried_without_retriggering_success(
    alert_context,
) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        await AlertService(session).create(
            user.id,
            AlertRuleCreateRequest(
                security_id=security.id,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        failed_provider = FakeProvider(FeishuWebhookError("temporary failure"))
        first = await AlertService(
            session,
            notification_provider=failed_provider,
        ).evaluate_user(user.id)
        notification = (await session.execute(select(Notification))).scalar_one()
        assert first[0].triggered is True
        assert notification.status == "FAILED"

        replacement_provider = FakeProvider()
        retried = await AlertService(
            session,
            notification_provider=replacement_provider,
        ).retry_notification(user.id, notification.id)
        assert retried.status == "SENT"
        assert replacement_provider.calls == 1

        # A second explicit retry and a normal active-edge evaluation are both
        # no-ops after the provider has already accepted the message.
        await AlertService(
            session,
            notification_provider=replacement_provider,
        ).retry_notification(user.id, notification.id)
        held = await AlertService(
            session,
            notification_provider=replacement_provider,
        ).evaluate_user(user.id)
        assert replacement_provider.calls == 1
        assert held[0].triggered is False


@pytest.mark.asyncio
async def test_provider_registry_can_replace_feishu_adapter(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    replacement_provider = FakeProvider()
    registry = NotificationProviderRegistry()
    registry.register("FEISHU", lambda _destination: replacement_provider)

    async with session_factory() as session:
        await AlertService(session, provider_registry=registry).create(
            user.id,
            AlertRuleCreateRequest(
                security_id=security.id,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        result = await AlertService(
            session,
            provider_registry=registry,
        ).evaluate_user(user.id)

        assert result[0].triggered is True
        assert replacement_provider.calls == 1


@pytest.mark.asyncio
async def test_evaluation_rejects_unknown_persisted_state(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        rule = AlertRule(
            user_id=user.id,
            security_id=security.id,
            condition_type="STATE",
            state_code="UNKNOWN_STATE",
            enabled=True,
        )
        session.add(rule)
        await session.commit()

        with pytest.raises(ApiError, match="State condition is invalid"):
            await AlertService(session).evaluate_rule(user.id, rule.id)


@pytest.mark.asyncio
async def test_settings_mask_and_clear_webhook(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    async with session_factory() as session:
        service = UserSettingsService(session)
        updated = await service.update(
            user.id,
            {"notification_settings": {"feishu_webhook": "https://example.test/hook/secret"}},
        )
        assert "secret" not in str(updated.settings)
        assert updated.settings["notification_settings"]["feishu_webhook_configured"] is True
        cleared = await service.update(
            user.id, {"notification_settings": {"feishu_webhook": None}}
        )
        assert cleared.settings["notification_settings"]["feishu_webhook_configured"] is False
        stored = (await session.execute(select(UserSetting))).scalar_one()
        assert stored.settings["notification_settings"]["feishu_webhook"] is None
