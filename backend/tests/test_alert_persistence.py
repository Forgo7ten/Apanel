"""Persistence, API and delivery coverage for Sprint 5 alerts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

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
from app.providers.notification import (
    FeishuWebhookError,
    NotificationMessage,
    NotificationProviderRegistry,
)
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import AlertService
from app.services.notification_service import (
    NOTIFICATION_PENDING_RECOVERY_MARGIN,
    NOTIFICATION_PENDING_RECOVERY_WINDOW,
    NOTIFICATION_PROVIDER_TIMEOUT,
    NotificationService,
    validate_pending_recovery_window,
)
from app.services.settings_service import UserSettingsService


class FakeProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def send(self, _user, _message) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, _user, _message) -> None:
        self.calls += 1
        self.started.set()
        await self.release.wait()


def test_pending_recovery_window_exceeds_provider_timeout_budget() -> None:
    minimum = NOTIFICATION_PROVIDER_TIMEOUT + NOTIFICATION_PENDING_RECOVERY_MARGIN

    assert NOTIFICATION_PENDING_RECOVERY_WINDOW > minimum
    with pytest.raises(ValueError):
        validate_pending_recovery_window(minimum)


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


def retry_message() -> NotificationMessage:
    return NotificationMessage(
        stock_name="贵州茅台",
        stock_code="600519",
        indicator="RSI",
        current_value=71.0,
        previous_value=69.0,
        change=2.0,
        date=date(2026, 9, 24),
    )


@pytest.mark.asyncio
async def test_recent_pending_notification_has_stable_retry_conflict(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="PENDING",
            created_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(notification)
        await session.commit()
        provider = FakeProvider()

        with pytest.raises(ApiError) as raised:
            await AlertService(session, notification_provider=provider).retry_notification(
                user.id, notification.id
            )

        assert raised.value.code == "NOTIFICATION_RETRY_CONFLICT"
        assert raised.value.status_code == 409
        assert provider.calls == 0


@pytest.mark.asyncio
async def test_expired_pending_notification_can_be_recovered(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="PENDING",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(notification)
        await session.commit()
        provider = FakeProvider()

        retried = await AlertService(session, notification_provider=provider).retry_notification(
            user.id, notification.id
        )

        assert retried.status == "SENT"
        assert provider.calls == 1


@pytest.mark.asyncio
async def test_sent_notification_retry_is_idempotent_and_user_scoped(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user_one = alert_context["user_one"]
    user_two = alert_context["user_two"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user_one.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="SENT",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
            sent_at=datetime.now(UTC),
        )
        session.add(notification)
        await session.commit()
        provider = FakeProvider()

        retried = await AlertService(session, notification_provider=provider).retry_notification(
            user_one.id, notification.id
        )
        assert retried.status == "SENT"
        assert provider.calls == 0

        with pytest.raises(ApiError) as raised:
            await AlertService(session, notification_provider=provider).retry_notification(
                user_two.id, notification.id
            )
        assert raised.value.code == "NOTIFICATION_NOT_FOUND"
        assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_retry_endpoint_returns_sent_notification_and_hides_other_users(
    alert_context,
) -> None:
    client = alert_context["client"]
    session_factory = alert_context["session_factory"]
    user_one = alert_context["user_one"]
    user_two = alert_context["user_two"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user_one.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="SENT",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
            sent_at=datetime.now(UTC),
        )
        session.add(notification)
        await session.commit()
        notification_id = notification.id

    response = await client.post(f"/api/v1/notifications/{notification_id}/retry")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "SENT"
    assert response.json()["data"]["error_code"] is None

    alert_context["current_user"]["value"] = user_two
    hidden = await client.post(f"/api/v1/notifications/{notification_id}/retry")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_notification_api_exposes_safe_error_fields_and_pending_retry_state(
    alert_context,
) -> None:
    client = alert_context["client"]
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        failed = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content={
                **retry_message().to_dict(),
                "error": {
                    "code": "FEISHU_ERROR",
                    "message": "webhook=https://hooks.example/secret-token",
                },
            },
            status="FAILED",
            error_code="FEISHU_ERROR",
            error_message="webhook=https://hooks.example/secret-token",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        pending = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="PENDING",
            created_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add_all([failed, pending])
        await session.commit()
        pending_id = pending.id

    records = await client.get("/api/v1/notifications")
    assert records.status_code == 200
    by_status = {item["status"]: item for item in records.json()["data"]}
    assert by_status["FAILED"]["error_code"] == "FEISHU_ERROR"
    assert by_status["FAILED"]["error_message"] == "Notification provider failed."
    assert by_status["FAILED"]["content"]["error"] == {
        "code": "FEISHU_ERROR",
        "message": "Notification provider failed.",
    }
    assert "secret-token" not in records.text
    assert by_status["FAILED"]["retryable"] is True
    assert by_status["PENDING"]["retryable"] is False

    conflict = await client.post(f"/api/v1/notifications/{pending_id}/retry")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "NOTIFICATION_RETRY_CONFLICT"


@pytest.mark.asyncio
async def test_concurrent_retries_do_not_post_twice(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="PENDING",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(notification)
        await session.commit()
        notification_id = notification.id

    provider = BlockingProvider()
    async with session_factory() as first_session, session_factory() as second_session:
        first = asyncio.create_task(
            AlertService(first_session, notification_provider=provider).retry_notification(
                user.id, notification_id
            )
        )
        await provider.started.wait()
        second = asyncio.create_task(
            AlertService(second_session, notification_provider=provider).retry_notification(
                user.id, notification_id
            )
        )
        await asyncio.sleep(0)
        assert provider.calls == 1
        provider.release.set()
        first_result, second_result = await asyncio.gather(first, second)

    assert first_result.status == second_result.status == "SENT"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_initial_delivery_serializes_stale_retry_before_provider_call(alert_context) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    provider = BlockingProvider()
    created_at = datetime.now(UTC) - timedelta(minutes=10)

    async with session_factory() as delivery_session, session_factory() as retry_session:
        delivery = asyncio.create_task(
            NotificationService(delivery_session, provider=provider).deliver(
                user_id=user.id,
                alert_rule_id=None,
                security_id=security.id,
                title="RSI >= 70",
                message=retry_message(),
                created_at=created_at,
            )
        )
        await provider.started.wait()
        async with session_factory() as observer:
            persisted = (await observer.execute(select(Notification))).scalar_one()
            notification_id = persisted.id

        retry = asyncio.create_task(
            AlertService(retry_session, notification_provider=provider).retry_notification(
                user.id, notification_id
            )
        )
        try:
            await asyncio.sleep(0.05)
            assert provider.calls == 1
            assert not retry.done()
        finally:
            provider.release.set()
            delivered, retried = await asyncio.gather(delivery, retry)

    assert delivered.status == retried.status == "SENT"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_terminal_commit_failure_relocks_and_persists_without_reposting(
    alert_context, monkeypatch
) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="PENDING",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(notification)
        await session.commit()
        provider = FakeProvider()
        original_commit = session.commit
        commit_calls = 0

        async def fail_once() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("commit failed after provider response")
            await original_commit()

        async def forbidden_get(*_args, **_kwargs):
            raise AssertionError("terminal recovery must re-lock with SELECT FOR UPDATE")

        monkeypatch.setattr(session, "commit", fail_once)
        monkeypatch.setattr(session, "get", forbidden_get)

        retried = await AlertService(session, notification_provider=provider).retry_notification(
            user.id, notification.id
        )

    assert retried.status == "SENT"
    assert provider.calls == 1
    assert commit_calls == 2


@pytest.mark.asyncio
async def test_terminal_commit_recovery_preserves_concurrent_terminal_winner(
    alert_context, monkeypatch
) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    async with session_factory() as session:
        notification = Notification(
            user_id=user.id,
            security_id=security.id,
            channel="FEISHU",
            title="RSI >= 70",
            content=retry_message().to_dict(),
            status="PENDING",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(notification)
        await session.commit()
        notification_id = notification.id

    provider = FakeProvider()
    rollback_done = asyncio.Event()
    winner_done = asyncio.Event()
    async with session_factory() as retry_session, session_factory() as winner_session:
        original_commit = retry_session.commit
        original_rollback = retry_session.rollback
        commit_calls = 0

        async def fail_terminal_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("ambiguous terminal commit")
            await original_commit()

        async def rollback_then_wait() -> None:
            await original_rollback()
            rollback_done.set()
            await winner_done.wait()

        monkeypatch.setattr(retry_session, "commit", fail_terminal_commit)
        monkeypatch.setattr(retry_session, "rollback", rollback_then_wait)
        retry = asyncio.create_task(
            AlertService(retry_session, notification_provider=provider).retry_notification(
                user.id, notification_id
            )
        )
        await rollback_done.wait()

        winner = (
            await winner_session.execute(
                select(Notification).where(Notification.id == notification_id)
            )
        ).scalar_one()
        winner.status = "FAILED"
        winner.error_code = "CONCURRENT_WINNER"
        winner.error_message = "Notification provider failed."
        winner.content = {
            **winner.content,
            "error": {
                "code": "CONCURRENT_WINNER",
                "message": "Notification provider failed.",
            },
        }
        await winner_session.commit()
        winner_done.set()
        retried = await retry

    assert retried.status == "FAILED"
    assert retried.error_code == "CONCURRENT_WINNER"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_terminal_commit_recovery_returns_safe_error_when_persistence_stays_down(
    alert_context, monkeypatch
) -> None:
    session_factory = alert_context["session_factory"]
    user = alert_context["user_one"]
    security = alert_context["security"]
    provider = FakeProvider()
    async with session_factory() as session:
        original_commit = session.commit
        commit_calls = 0

        async def fail_terminal_commits() -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                await original_commit()
                return
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(session, "commit", fail_terminal_commits)
        with pytest.raises(ApiError) as raised:
            await NotificationService(session, provider=provider).deliver(
                user_id=user.id,
                alert_rule_id=None,
                security_id=security.id,
                title="RSI >= 70",
                message=retry_message(),
            )

    assert raised.value.code == "NOTIFICATION_PERSISTENCE_FAILED"
    assert raised.value.status_code == 503
    assert raised.value.message == "Notification delivery outcome could not be saved."
    assert provider.calls == 1
    assert commit_calls > 1


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
