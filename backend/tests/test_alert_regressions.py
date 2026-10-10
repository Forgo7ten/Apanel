"""Previously reproduced alert and outbox defects must stay fixed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.models import AlertRule, IndicatorSnapshot, Notification, Security
from app.providers.notification import FeishuWebhookError
from app.repositories.alert import AlertRepository
from app.schemas.alerts import AlertRuleCreateRequest, AlertRuleUpdateRequest
from app.schemas.state_row import StateRow
from app.services.alert_observation_service import AlertObservationService
from app.services.alert_service import AlertService, _condition_fields
from app.services.indicator_service import IndicatorService
from app.services.notification_service import NotificationService, dispatch_pending_notifications
from app.services.state_service import state_parameter_key
from tests.state_fixture import seed_current_state
from tests.test_alert_persistence import FakeProvider
from tests.test_alert_persistence import alert_context as alert_context
from tests.test_indicator_state_persistence import indicator_context as indicator_context

pytestmark = pytest.mark.asyncio


async def test_scalar_ma_alert_resolves_equivalent_single_period_variant(indicator_context):
    async with indicator_context() as session:
        await IndicatorService(session).calculate("600519")
        fields = _condition_fields(
            condition_type="VALUE",
            state_id=None,
            state_code=None,
            indicator="MA",
            operator=">",
            threshold=0,
            parameters={"period": 5},
            field=None,
            adjust_type="qfq",
        )
        rule = SimpleNamespace(security_id=1, **fields)
        result = await AlertObservationService(session).resolve_value(rule)
        assert result is not None, (
            "MA(period=5) rule cannot resolve stored MA(periods=[5]) projection"
        )


async def test_retarget_rule_away_and_back_does_not_reuse_notification_sequence(alert_context):
    factory = alert_context["session_factory"]
    uid = alert_context["user_one"].id
    sid = alert_context["security"].id
    provider = FakeProvider()
    async with factory() as session:
        other = Security(
            symbol="000001",
            name="Other",
            market="SZ",
            exchange="SZ",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add(other)
        await session.commit()
        other_id = other.id
        rule = await AlertService(session).create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        rule_id = rule.id
    async with factory() as session:
        first = await AlertService(session, notification_provider=provider).evaluate_rule(
            uid, rule_id
        )
        assert first.triggered
    async with factory() as session:
        await AlertService(session).update(
            uid, rule_id, AlertRuleUpdateRequest(security_id=other_id)
        )
    async with factory() as session:
        await AlertService(session).update(uid, rule_id, AlertRuleUpdateRequest(security_id=sid))
    async with factory() as session:
        second = await AlertService(session, notification_provider=provider).evaluate_rule(
            uid, rule_id
        )
        assert second.triggered


async def test_locked_rule_read_refreshes_preloaded_identity_map(alert_context):
    factory = alert_context["session_factory"]
    uid = alert_context["user_one"].id
    sid = alert_context["security"].id
    async with factory() as session:
        created = await AlertService(session).create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        rid = created.id
    async with factory() as reader:
        old = await AlertRepository(reader).get_owned(rid, uid)
        assert old.enabled
        await reader.commit()  # expire_on_commit=False, as in deployed session factory.
        async with factory() as writer:
            await writer.execute(update(AlertRule).where(AlertRule.id == rid).values(enabled=False))
            await writer.commit()
        locked = await AlertRepository(reader).get_for_evaluation(rid)
        assert not locked.enabled, "FOR UPDATE did not refresh already-loaded ORM attributes"


async def test_malformed_pending_notification_does_not_block_valid_successor(alert_context):
    from app.services.notification_service import (
        dispatch_pending_notifications,
    )

    factory = alert_context["session_factory"]
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    created_at = datetime.now(UTC) - timedelta(minutes=30)
    good = {
        "stock_name": "Test",
        "stock_code": "600519",
        "indicator": "RSI",
        "current_value": 71,
        "date": "2026-09-24",
    }
    async with factory() as session:
        bad = Notification(
            user_id=uid,
            security_id=sid,
            channel="FEISHU",
            title="bad",
            content={**good, "date": "bad-date"},
            status="PENDING",
            created_at=created_at,
        )
        successor = Notification(
            user_id=uid,
            security_id=sid,
            channel="FEISHU",
            title="good",
            content=good,
            status="PENDING",
            created_at=created_at + timedelta(seconds=1),
        )
        session.add_all([bad, successor])
        await session.commit()
    provider = FakeProvider()
    async with factory() as session:
        await dispatch_pending_notifications(session, provider=provider)
    assert provider.calls == 1


async def test_unrecognized_ma_target_key_is_not_silently_repaired(indicator_context):
    async with indicator_context() as session:
        await IndicatorService(session).calculate("600519")
        rule = SimpleNamespace(
            security_id=1,
            condition_type="VALUE",
            indicator_type="MA",
            parameters={"period": 5},
            parameter_key="invalid-target-key",
            field="MA5",
            adjust_type="qfq",
        )
        assert await AlertObservationService(session).resolve_value(rule) is None


@pytest.mark.parametrize(
    "adjust,parameters", [("none", {}), ("qfq", {"period": 20, "multiplier": 3})]
)
async def test_state_current_target_cannot_cross_adjustment_or_parameters(
    alert_context, adjust, parameters
):
    async with alert_context["session_factory"]() as session:
        service = AlertService(session)
        created = await service.create(
            alert_context["user_one"].id,
            AlertRuleCreateRequest(
                security_id=alert_context["security"].id,
                condition_type="STATE",
                state_id="BOLL_WIDTH_NARROWING",
                adjust_type=adjust,
                parameters=parameters,
            ),
        )
        result = await service.evaluate_rule(
            alert_context["user_one"].id, created.id, provider=FakeProvider()
        )
        assert not result.triggered


async def test_crossing_rule_ignores_persistent_above_relation(alert_context):
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        params = {"short_period": 5, "long_period": 10}
        state = StateRow(
            security_id=sid,
            trade_date=date(2026, 9, 24),
            state_code="MA_CROSS_UP",
            indicator_type="MA",
            adjust_type="qfq",
            status="ACTIVE",
            parameters=params,
            parameter_key=state_parameter_key("MA_CROSS_UP", params),
            metadata={"active": True, "transition": False},
        )
        state = await seed_current_state(session, state)
        await session.commit()
        service = AlertService(session)
        rule = await service.create(
            uid,
            AlertRuleCreateRequest(security_id=sid, condition_type="STATE", state_id="MA_CROSS_UP"),
        )
        provider = FakeProvider()
        assert not (await service.evaluate_rule(uid, rule.id, provider=provider)).triggered
        state.trade_date = date(2026, 9, 25)
        state.metadata_json = {"active": True, "transition": True}
        await session.commit()
        assert (await service.evaluate_rule(uid, rule.id, provider=provider)).triggered
        assert provider.calls == 1


async def test_scheduled_correction_updates_baseline_without_renotifying(alert_context):
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        service = AlertService(session)
        await service.create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        day = date(2026, 9, 24)
        assert (await service.evaluate_all(observation_date=day))[0].triggered
        snapshot = (await session.execute(select(IndicatorSnapshot))).scalar_one()
        snapshot.values = {key: {"value": 50.0} for key in snapshot.values}
        await session.commit()
        assert not (await service.evaluate_all(observation_date=day))[0].triggered
        snapshot.values = {key: {"value": 75.0} for key in snapshot.values}
        await session.commit()
        assert not (await service.evaluate_all(observation_date=day))[0].triggered
        assert len((await session.execute(select(Notification))).scalars().all()) == 1


async def test_invalid_rule_does_not_abort_other_rules_in_scheduled_batch(alert_context):
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        session.add(
            AlertRule(
                user_id=uid, security_id=sid, condition_type="STATE", state_code="REMOVED_PLUGIN"
            )
        )
        await session.commit()
        service = AlertService(session)
        await service.create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        result = await service.evaluate_all(observation_date=date(2026, 9, 24))
        assert result[0].error_code == "INVALID_ALERT_RULE"
        assert result[1].triggered


async def test_transient_failure_is_retried_only_when_due_and_keeps_identity(alert_context):
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    factory = alert_context["session_factory"]
    async with factory() as session:
        service = AlertService(session)
        rule = await service.create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        result = await service.evaluate_rule(
            uid, rule.id, provider=FakeProvider(FeishuWebhookError("retry", status_code=503))
        )
        record = await session.get(Notification, result.notification_id)
        assert record.status == "FAILED"
        assert record.attempt_count == 1
        due = record.next_retry_at
        event_id = record.content["event_id"]
        good = FakeProvider()
        early = await dispatch_pending_notifications(
            session, provider=good, now=due - timedelta(seconds=1)
        )
        assert early["total"] == 0
        sent = await dispatch_pending_notifications(
            session, provider=good, now=due + timedelta(seconds=1)
        )
        assert sent["sent"] == 1
        assert good.calls == 1
        await session.refresh(record)
        assert record.content["event_id"] == event_id
        assert record.next_retry_at is None
        assert record.attempt_count == 2
        again = await dispatch_pending_notifications(
            session, provider=good, now=due + timedelta(minutes=10)
        )
        assert again["total"] == 0


async def test_manual_retry_preserves_event_id(alert_context):
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        service = AlertService(session)
        rule = await service.create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid,
                condition_type="VALUE",
                indicator="RSI",
                operator=">=",
                threshold=70,
            ),
        )
        result = await service.evaluate_rule(
            uid, rule.id, provider=FakeProvider(FeishuWebhookError("failure"))
        )
        record = await session.get(Notification, result.notification_id)
        old_id = record.content["event_id"]
        await service.retry_notification(uid, record.id, provider=FakeProvider())
        assert record.content["event_id"] == old_id


async def test_failed_retry_commit_recovery_does_not_restore_previous_failure(
    alert_context, monkeypatch
):
    from tests.test_alert_persistence import retry_message

    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        notification = Notification(
            user_id=uid,
            security_id=sid,
            channel="FEISHU",
            title="Retry",
            content=retry_message().to_dict(),
            status="FAILED",
            error_code="HTTP_503",
            attempt_count=1,
        )
        session.add(notification)
        await session.commit()
        original = session.commit
        commits = 0

        async def fail_once():
            nonlocal commits
            commits += 1
            if commits == 1:
                raise RuntimeError("commit interrupted")
            await original()

        monkeypatch.setattr(session, "commit", fail_once)
        provider = FakeProvider()
        result = await NotificationService(session).retry(
            user_id=uid, notification_id=notification.id, provider=provider
        )
        assert result.status == "SENT"
        assert result.attempt_count == 2
        assert provider.calls == 1
        assert commits == 2
