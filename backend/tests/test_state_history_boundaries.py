"""History range anchors and pagination must survive non-trading days and corrections."""

from datetime import date

import httpx
import pytest
from sqlalchemy import delete, select, update

from app.models import AnalysisStream, DailyBar, TradingCalendar
from app.services.analysis_service import AnalysisService
from app.services.state_service import StateService
from tests.test_alert_persistence import alert_context as alert_context
from tests.test_indicator_state_persistence import indicator_context as indicator_context
from tests.test_state_events import publish, row


@pytest.mark.parametrize("start", [date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 2)])
async def test_history_closed_start_uses_predecessor_of_first_observation(indicator_context, start):
    factory = indicator_context
    weekend = [date(2026, 1, 31), date(2026, 2, 1)]
    async with factory() as session:
        await session.execute(delete(DailyBar).where(DailyBar.trade_date.in_(weekend)))
        await session.execute(
            update(TradingCalendar)
            .where(TradingCalendar.trade_date.in_(weekend))
            .values(status="CLOSED", expected_open=False, actual_open=False)
        )
        await session.commit()
        await AnalysisService(session).materialize("600519", end=date(2026, 2, 9))
    async with factory() as session:
        full = await StateService(session).history(
            "600519", start=date(2026, 1, 29), end=date(2026, 2, 5), state_code="MA_CROSS_UP"
        )
        clipped = await StateService(session).history(
            "600519", start=start, end=date(2026, 2, 5), state_code="MA_CROSS_UP"
        )
        expected = [item for item in full if item.trade_date >= start]
        assert clipped[0].trade_date == date(2026, 2, 2)
        assert [(x.trade_date, x.status, x.metadata) for x in clipped] == [
            (x.trade_date, x.status, x.metadata) for x in expected
        ]


async def test_event_pagination_detects_removal_without_new_event(alert_context):
    sid = alert_context["security"].id
    factory = alert_context["session_factory"]
    first, second = date(2026, 9, 1), date(2026, 9, 2)
    async with factory() as session:
        await publish(session, sid, [row(sid, first, "INACTIVE"), row(sid, second, "ACTIVE")])
        seq = (
            await session.execute(
                select(AnalysisStream.event_sequence).where(
                    AnalysisStream.security_id == sid, AnalysisStream.adjust_type == "qfq"
                )
            )
        ).scalar_one()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=alert_context["app"]), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/securities/600519/states/events", params={"limit": 1})
        assert response.status_code == 200, response.text
        page = response.json()["data"]
        async with factory() as session:
            # Correct the ENTER away: this only invalidates an old event, allocates no new seq.
            assert await publish(session, sid, [row(sid, second, "INACTIVE")], start=second) == 0
            current_seq = (
                await session.execute(
                    select(AnalysisStream.event_sequence).where(
                        AnalysisStream.security_id == sid, AnalysisStream.adjust_type == "qfq"
                    )
                )
            ).scalar_one()
            assert current_seq == seq
        stale = await client.get(
            "/api/v1/securities/600519/states/events",
            params={"after_seq": page["next_cursor"], "generation": page["generation"]},
        )
        assert stale.status_code == 409, stale.text


async def test_rolling_default_window_invalidates_pagination_without_new_events(alert_context):
    sid = alert_context["security"].id
    factory = alert_context["session_factory"]
    first, second, third = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    async with factory() as session:
        await publish(session, sid, [row(sid, first, "INACTIVE"), row(sid, second, "ACTIVE")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=alert_context["app"]), base_url="http://test"
    ) as client:
        initial = await client.get("/api/v1/securities/600519/states/events", params={"limit": 1})
        assert initial.status_code == 200, initial.text
        page = initial.json()["data"]
        async with factory() as session:
            assert await publish(session, sid, [row(sid, third, "ACTIVE")], start=third) == 0
        later = await client.get(
            "/api/v1/securities/600519/states/events",
            params={"generation": page["generation"], "after_seq": page["next_cursor"]},
        )
        assert later.status_code == 409
