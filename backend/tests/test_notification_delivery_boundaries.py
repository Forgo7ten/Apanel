"""Delivery diagnostics must survive retries and provider cleanup must be bounded."""

import asyncio
from datetime import date

import pytest

from app.providers.notification import FeishuWebhookError
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import AlertService
from app.services.notification_service import NotificationService
from tests.test_alert_persistence import FakeProvider
from tests.test_alert_persistence import alert_context as alert_context


async def pending(factory, uid, sid):
    async with factory() as session:
        await AlertService(session).create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        result = (await AlertService(session).evaluate_all(observation_date=date(2026, 9, 24)))[0]
        return result.notification_id


async def test_event_id_reaches_provider_and_is_preserved_by_retry(alert_context):
    factory = alert_context["session_factory"]
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    nid = await pending(factory, uid, sid)
    failed = FakeProvider(FeishuWebhookError("test failure"))
    async with factory() as session:
        first = await NotificationService(session).dispatch(nid, provider=failed)
        event_id = first.content["event_id"]
    succeeded = FakeProvider()
    async with factory() as session:
        second = await NotificationService(session).retry(
            user_id=uid, notification_id=nid, provider=succeeded
        )
        assert second.content["event_id"] == event_id
        assert getattr(failed.messages[0], "event_id", None) == event_id
        assert getattr(succeeded.messages[0], "event_id", None) == event_id
        assert event_id in succeeded.messages[0].render_text()


async def test_owned_provider_cleanup_cannot_block_terminal_commit(alert_context, monkeypatch):
    import app.services.notification_service as module

    factory = alert_context["session_factory"]
    nid = await pending(factory, alert_context["user_one"].id, alert_context["security"].id)
    monkeypatch.setattr(module, "NOTIFICATION_CLEANUP_TIMEOUT_SECONDS", 0.02, raising=False)

    class Provider(FakeProvider):
        async def close(self):
            await asyncio.Event().wait()

    provider = Provider()
    async with factory() as session:
        service = NotificationService(session, provider_factory=lambda _: provider)
        result = await asyncio.wait_for(service.dispatch(nid), 1)
        assert result.status == "SENT"
    assert provider.calls == 1


def test_message_rejects_unbounded_or_control_character_event_id():
    from app.providers.notification import NotificationMessage

    for event_id in ("x" * 161, "fake\nsecond-line", "https://invalid.example/token"):
        with pytest.raises(ValueError):
            NotificationMessage(
                stock_name="Test",
                stock_code="600519",
                indicator="RSI",
                date=date(2026, 9, 24),
                event_id=event_id,
            )
