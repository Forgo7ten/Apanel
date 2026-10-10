"""Compact timeline, quality gaps, idempotence, migration and history contracts."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import delete, event, func, select

from app.core.errors import ApiError
from app.db.base import Base
from app.models import AnalysisStream, CurrentIndicatorState, StateEvent
from app.repositories.state_event import StateEventRepository
from app.schemas.state_row import StateRow
from app.services.analysis_control import lock_analysis_stream
from app.services.analysis_service import AnalysisService
from app.services.state_service import StateService, state_parameter_key
from tests.test_alert_persistence import alert_context as alert_context
from tests.test_indicator_state_persistence import indicator_context as indicator_context

PARAMETERS = {"period": 20, "multiplier": 2.0}
CODE = "BOLL_WIDTH_NARROWING"
KEY = state_parameter_key(CODE, PARAMETERS)


def row(sid, day, status, *, transition=False):
    return StateRow(
        security_id=sid,
        trade_date=day,
        state_code=CODE,
        indicator_type="BOLL",
        parameter_key=KEY,
        parameters=PARAMETERS,
        adjust_type="qfq",
        status=status,
        metadata={
            "name": "Narrowing",
            "active": status == "ACTIVE",
            "transition": transition,
            "reason": "missing_values" if status == "UNKNOWN" else None,
            "value_context": {
                "width": {"current_value": 0.1, "previous_value": 0.2, "change": -0.1}
            },
        },
    )


async def publish(session, sid, observations, *, start=None, through=None):
    stream = await lock_analysis_stream(session, sid, "qfq")
    # The alert fixture seeds a standalone Current State at 2026-09-24 for
    # alert endpoint tests. Event timeline tests deliberately start earlier;
    # discard that unrelated test-only seed before publishing a clean series.
    latest = through or max(item.trade_date for item in observations)
    if stream.event_sequence == 0 and stream.state_through and stream.state_through > latest:
        await session.execute(
            delete(CurrentIndicatorState).where(
                CurrentIndicatorState.security_id == sid,
                CurrentIndicatorState.adjust_type == "qfq",
            )
        )
        stream.state_through = None
    result = await StateEventRepository(session).publish(
        stream,
        observations,
        start=start,
        through=latest,
    )
    stream.state_through = latest
    await session.commit()
    return result


async def test_compact_timeline_keeps_unknown_distinct_and_recovers_without_false_enter(
    alert_context,
):
    sid = alert_context["security"].id
    start = date(2026, 9, 1)
    statuses = ["INACTIVE", "ACTIVE", "UNKNOWN", "ACTIVE", "ACTIVE", "INACTIVE", "ACTIVE"]
    rows = [
        row(sid, start + timedelta(days=i), status, transition=i in (1, 6))
        for i, status in enumerate(statuses)
    ]
    async with alert_context["session_factory"]() as session:
        assert await publish(session, sid, rows) == 6
        events = (
            (await session.execute(select(StateEvent).order_by(StateEvent.stream_seq)))
            .scalars()
            .all()
        )
        assert [item.event_kind for item in events] == [
            "BASELINE",
            "ENTER",
            "UNKNOWN",
            "RECOVER",
            "EXIT",
            "ENTER",
        ]
        recover = events[3]
        assert (
            recover.status == "ACTIVE" and recover.since_date is None and not recover.since_is_exact
        )
        assert (
            await session.execute(select(func.count()).select_from(CurrentIndicatorState))
        ).scalar_one() == 1
        assert "indicator_states" not in Base.metadata.tables
        current = (await StateEventRepository(session).current_many([sid]))[sid][0]
        assert current.since_date == start + timedelta(days=6)
        assert current.since_is_exact
        assert current.trade_date == start + timedelta(days=6)


async def test_identical_replay_preserves_event_ids_and_sequence(alert_context):
    sid = alert_context["security"].id
    days = [
        row(sid, date(2026, 9, 1), "INACTIVE"),
        row(sid, date(2026, 9, 2), "ACTIVE", transition=True),
    ]
    async with alert_context["session_factory"]() as session:
        await publish(session, sid, days)
        ids = (await session.execute(select(StateEvent.id))).scalars().all()
        seq = (
            await session.execute(
                select(AnalysisStream.event_sequence).where(AnalysisStream.adjust_type == "qfq")
            )
        ).scalar_one()
        assert await publish(session, sid, days) == 0
        assert (await session.execute(select(StateEvent.id))).scalars().all() == ids
        assert (
            await session.execute(
                select(AnalysisStream.event_sequence).where(AnalysisStream.adjust_type == "qfq")
            )
        ).scalar_one() == seq


async def test_continuous_state_updates_current_metadata_without_appending_event(alert_context):
    sid = alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        await publish(
            session,
            sid,
            [
                row(sid, date(2026, 9, 1), "INACTIVE"),
                row(sid, date(2026, 9, 2), "ACTIVE", transition=True),
            ],
        )
        next_row = row(sid, date(2026, 9, 3), "ACTIVE")
        next_row.metadata_json = {
            **next_row.metadata_json,
            "value_context": {
                "width": {"current_value": 0.05, "previous_value": 0.1, "change": -0.05}
            },
        }
        assert await publish(session, sid, [next_row], start=next_row.trade_date) == 0
        current = (await StateEventRepository(session).current_many([sid]))[sid][0]
        assert current.trade_date == date(2026, 9, 3)
        assert current.metadata["value_context"]["width"]["current_value"] == 0.05
        assert current.since_date == date(2026, 9, 2)


async def test_restatement_archives_old_events_and_retains_trusted_prefix(alert_context):
    sid = alert_context["security"].id
    d1, d2, d3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    async with alert_context["session_factory"]() as session:
        await publish(
            session,
            sid,
            [
                row(sid, d1, "INACTIVE"),
                row(sid, d2, "ACTIVE", transition=True),
                row(sid, d3, "ACTIVE"),
            ],
        )
        old = (
            (await session.execute(select(StateEvent).order_by(StateEvent.stream_seq)))
            .scalars()
            .all()
        )
        old_ids = [item.id for item in old]
        await publish(
            session,
            sid,
            [row(sid, d2, "INACTIVE"), row(sid, d3, "ACTIVE", transition=True)],
            start=d2,
        )
        active = (
            (
                await session.execute(
                    select(StateEvent)
                    .where(StateEvent.valid.is_(True))
                    .order_by(StateEvent.stream_seq)
                )
            )
            .scalars()
            .all()
        )
        assert active[0].id == old_ids[0]
        assert active[-1].trade_date == d3 and active[-1].is_restatement
        await session.refresh(old[-1])
        assert not old[-1].valid
        assert active[-1].stream_seq > old[-1].stream_seq
        with pytest.raises(ApiError, match="regress"):
            await publish(session, sid, [row(sid, d1, "ACTIVE")], through=d1)
        await session.rollback()


async def test_asof_seed_finds_enter_before_history_window(alert_context):
    sid = alert_context["security"].id
    async with alert_context["session_factory"]() as session:
        await publish(
            session,
            sid,
            [
                row(sid, date(2026, 5, 1), "INACTIVE"),
                row(sid, date(2026, 5, 2), "ACTIVE", transition=True),
            ],
        )
        repository = StateEventRepository(session)
        assert (
            await repository.events(sid, "qfq", start=date(2026, 6, 1), end=date(2026, 6, 30)) == []
        )
        seed = await repository.seed_at(sid, "qfq", date(2026, 5, 31))
        assert seed[0].status == "ACTIVE"
        assert seed[0].since_date == date(2026, 5, 2)


async def test_daily_history_reconstruction_preserves_values_and_never_writes(indicator_context):
    factory = indicator_context
    async with factory() as session:
        await AnalysisService(session).materialize("600519", end=date(2026, 2, 9))
    statements = []
    engine = factory.kw["bind"]

    def capture(_connection, _cursor, sql, *_args):
        statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with factory() as session:
            history = await StateService(session).history(
                "600519", start=date(2026, 2, 1), end=date(2026, 2, 9), state_code="MA_CROSS_UP"
            )
            assert len(history) == 9
            assert all(item.status == "ACTIVE" for item in history)
            assert all(not item.metadata["transition"] for item in history)
            assert all("MA5" in item.metadata["value_context"] for item in history)
        assert all(
            not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for sql in statements
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_history_refuses_mixed_source_revisions_during_rebuild(indicator_context):
    async with indicator_context() as session:
        await AnalysisService(session).materialize("600519", end=date(2026, 2, 9))
        stream = (await session.execute(select(AnalysisStream))).scalar_one()
        stream.source_revision += 1
        stream.dirty_from = date(2026, 2, 1)
        await session.commit()
        with pytest.raises(ApiError) as error:
            await StateService(session).history("600519")
        assert error.value.code == "ANALYSIS_REBUILD_PENDING"


async def test_event_api_pagination_detects_timeline_changes(alert_context):
    sid = alert_context["security"].id
    factory = alert_context["session_factory"]
    async with factory() as session:
        await publish(
            session,
            sid,
            [
                row(sid, date(2026, 9, 1), "INACTIVE"),
                row(sid, date(2026, 9, 2), "ACTIVE", transition=True),
            ],
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=alert_context["app"]), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/securities/600519/states/events", params={"limit": 1})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["next_cursor"] is not None
        async with factory() as session:
            await publish(
                session, sid, [row(sid, date(2026, 9, 3), "INACTIVE")], start=date(2026, 9, 3)
            )
        stale = await client.get(
            "/api/v1/securities/600519/states/events",
            params={"generation": data["generation"], "after_seq": data["next_cursor"]},
        )
        assert stale.status_code == 409
