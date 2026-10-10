"""Rule cursor + outbox recovery when EOD observations were not evaluated daily."""

from datetime import date, timedelta

from sqlalchemy import select

from app.models import AlertInstance, Notification
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import AlertService
from app.tasks.pipeline import _notification_ids
from tests.snapshot_fixture import current_snapshot
from tests.test_alert_persistence import alert_context as alert_context
from tests.test_state_events import publish, row


async def test_state_catchup_consumes_missed_exit_and_two_enters_exactly_once(alert_context):
    factory = alert_context["session_factory"]
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    day = date(2026, 9, 24)
    async with factory() as session:
        service = AlertService(session)
        await service.create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid, condition_type="STATE", state_id="BOLL_WIDTH_NARROWING"
            ),
        )
        await publish(session, sid, [row(sid, day, "INACTIVE")])
        assert not (await service.evaluate_all(observation_date=day))[0].triggered
        await publish(
            session,
            sid,
            [
                row(sid, day + timedelta(days=1), "ACTIVE", transition=True),
                row(sid, day + timedelta(days=2), "INACTIVE"),
                row(sid, day + timedelta(days=3), "ACTIVE", transition=True),
            ],
            start=day + timedelta(days=1),
        )
        results = await service.evaluate_all(observation_date=day + timedelta(days=3))
        assert len(results[0].notification_ids) == 2
        assert len(_notification_ids(results)) == 2
        notifications = (
            (await session.execute(select(Notification).order_by(Notification.id))).scalars().all()
        )
        assert [item.observation_date for item in notifications] == [
            day + timedelta(days=1),
            day + timedelta(days=3),
        ]
        assert all(item.content.get("source_event_id") for item in notifications)
        assert not (await service.evaluate_all(observation_date=day + timedelta(days=3)))[
            0
        ].triggered
        assert (
            await session.execute(select(AlertInstance.last_consumed_state_seq))
        ).scalar_one() > 0


async def test_value_catchup_replays_actual_snapshots_not_current_only(alert_context):
    factory = alert_context["session_factory"]
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    day = date(2026, 9, 24)
    async with factory() as session:
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
        assert (await service.evaluate_all(observation_date=day))[0].triggered
        for offset, value in enumerate([60, 75, 55, 80], 1):
            session.add(
                current_snapshot(
                    security_id=sid,
                    trade_date=day + timedelta(days=offset),
                    indicator_type="RSI",
                    adjust_type="qfq",
                    parameters={"period": 14},
                    values={"value": value},
                )
            )
        await session.commit()
        result = (await service.evaluate_all(observation_date=day + timedelta(days=4)))[0]
        assert len(result.notification_ids) == 2
        notifications = (
            (await session.execute(select(Notification).order_by(Notification.id))).scalars().all()
        )
        assert len(notifications) == 3
        assert [item.trigger_sequence for item in notifications] == [1, 2, 3]
        assert not (await service.evaluate_all(observation_date=day + timedelta(days=4)))[
            0
        ].triggered


async def test_value_restatement_updates_baseline_without_historical_notification(alert_context):
    from app.indicators.parameters import parameter_key

    factory = alert_context["session_factory"]
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    day = date(2026, 9, 24)
    async with factory() as session:
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
        await service.evaluate_all(observation_date=day)
        key = parameter_key("RSI", {"period": 14})
        for offset, value in enumerate([60, 75], 1):
            session.add(
                current_snapshot(
                    security_id=sid,
                    trade_date=day + timedelta(days=offset),
                    indicator_type="RSI",
                    adjust_type="qfq",
                    parameters={
                        "_format": 2,
                        "variants": {key: {"period": 14}},
                        "_source": {
                            "revision": 2,
                            "restated_through": (day + timedelta(days=2)).isoformat(),
                        },
                    },
                    values={key: {"value": value}},
                )
            )
        await session.commit()
        result = (await service.evaluate_all(observation_date=day + timedelta(days=2)))[0]
        assert not result.triggered
        assert (await session.execute(select(AlertInstance.status))).scalar_one() == "ACTIVE"
        assert len((await session.execute(select(Notification))).scalars().all()) == 1


async def test_known_missed_enter_is_delivered_even_if_latest_state_is_unknown(alert_context):
    factory = alert_context["session_factory"]
    uid, sid = alert_context["user_one"].id, alert_context["security"].id
    day = date(2026, 9, 24)
    async with factory() as session:
        service = AlertService(session)
        await service.create(
            uid,
            AlertRuleCreateRequest(
                security_id=sid, condition_type="STATE", state_id="BOLL_WIDTH_NARROWING"
            ),
        )
        await publish(session, sid, [row(sid, day, "INACTIVE")])
        assert not (await service.evaluate_all(observation_date=day))[0].triggered
        await publish(
            session,
            sid,
            [
                row(sid, day + timedelta(days=1), "ACTIVE", transition=True),
                row(sid, day + timedelta(days=2), "UNKNOWN"),
            ],
            start=day + timedelta(days=1),
        )
        result = (await service.evaluate_all(observation_date=day + timedelta(days=2)))[0]
        assert len(result.notification_ids) == 1
        instance = (await session.execute(select(AlertInstance))).scalar_one()
        assert instance.status == "ACTIVE"  # Latest unknown did not become a fake EXIT.
        assert instance.last_evaluated_trade_date == day + timedelta(days=2)
        again = (await service.evaluate_all(observation_date=day + timedelta(days=2)))[0]
        assert not again.triggered
