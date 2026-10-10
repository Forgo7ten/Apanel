"""Run only against APANEL_TEST_POSTGRES_URL, a disposable database.

The notification race disables the in-process lock so only PostgreSQL protects it.
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import func, select

from app.models import AlertRule, Notification, Security, User, UserStatus
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import AlertService
from app.services.notification_service import NotificationService
from app.services.settings_service import UserSettingsService
from tests.snapshot_fixture import current_snapshot
from tests.test_alert_persistence import FakeProvider
from tests.test_auth_postgres_concurrency import postgres_engine as postgres_engine
from tests.test_auth_postgres_concurrency import (
    postgres_session_factory as postgres_session_factory,
)


async def _seed(factory):
    async with factory() as session:
        user = User(
            username="race-user",
            email="race@example.com",
            password_hash="unused",
            status=UserStatus.ACTIVE,
        )
        security = Security(
            symbol="600519",
            name="Race",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add_all([user, security])
        await session.flush()
        session.add(
            current_snapshot(
                security_id=security.id,
                trade_date=date(2026, 9, 24),
                indicator_type="RSI",
                adjust_type="qfq",
                parameters={"period": 14},
                values={"value": 71},
                previous_values={"value": 69},
                delta={"value": 2},
            )
        )
        await session.commit()
        rule = await AlertService(session).create(
            user.id,
            AlertRuleCreateRequest(
                security_id=security.id,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        return user.id, rule.id


async def test_two_evaluators_claim_one_edge(postgres_session_factory):
    factory = postgres_session_factory
    uid, rid = await _seed(factory)
    barrier = asyncio.Barrier(2)
    provider = FakeProvider()

    async def evaluate():
        async with factory() as session:
            await barrier.wait()
            return await AlertService(session).evaluate_rule(uid, rid, provider=provider)

    results = await asyncio.wait_for(asyncio.gather(evaluate(), evaluate()), 10)
    assert sum(result.triggered for result in results) == 1
    assert provider.calls == 1
    async with factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(Notification))
        ).scalar_one() == 1
        assert (await session.get(AlertRule, rid)).trigger_sequence == 1


async def test_postgres_notification_row_lock_prevents_duplicate_posts(
    postgres_session_factory, monkeypatch
):
    factory = postgres_session_factory
    uid, rid = await _seed(factory)
    async with factory() as session:
        result = (await AlertService(session).evaluate_all(observation_date=date(2026, 9, 24)))[0]
        notification_id = result.notification_id

    class NoLocalLock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.services.notification_service._retry_lock", lambda _: NoLocalLock())

    class Provider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send(self, *args):
            self.calls += 1
            self.started.set()
            await self.release.wait()

    provider = Provider()

    async def dispatch():
        async with factory() as session:
            return (
                await NotificationService(session).dispatch(notification_id, provider=provider)
            ).status

    first = asyncio.create_task(dispatch())
    await asyncio.wait_for(provider.started.wait(), 5)
    second = asyncio.create_task(dispatch())
    try:
        await asyncio.sleep(0.1)
        assert provider.calls == 1
    finally:
        provider.release.set()
    assert await asyncio.wait_for(asyncio.gather(first, second), 5) == ["SENT", "SENT"]
    assert provider.calls == 1


async def test_settings_concurrent_first_write_merges_under_owner_lock(postgres_session_factory):
    factory = postgres_session_factory
    uid, _ = await _seed(factory)
    barrier = asyncio.Barrier(2)

    async def change(payload):
        async with factory() as session:
            await barrier.wait()
            await UserSettingsService(session).update(uid, payload)

    await asyncio.wait_for(
        asyncio.gather(
            change({"display_settings": {"density": "compact"}}), change({"adjust_type": "none"})
        ),
        10,
    )
    async with factory() as session:
        settings = (await UserSettingsService(session).get(uid)).settings
        assert settings["adjust_type"] == "none"
        assert settings["display_settings"]["density"] == "compact"
