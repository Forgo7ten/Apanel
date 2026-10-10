"""Seed the *new* Current State store for API tests without old daily rows."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalysisStream, CurrentIndicatorState
from app.schemas.state_row import StateRow
from app.services.analysis_control import insert_for


async def seed_current_state(session: AsyncSession, row: StateRow) -> CurrentIndicatorState:
    await session.execute(
        insert_for(session, AnalysisStream)
        .values(security_id=row.security_id, adjust_type=row.adjust_type)
        .on_conflict_do_nothing()
    )
    stream = await session.get(AnalysisStream, (row.security_id, row.adjust_type))
    assert stream is not None
    if stream.state_through is None or row.trade_date > stream.state_through:
        stream.state_through = row.trade_date
    model = CurrentIndicatorState(
        security_id=row.security_id,
        adjust_type=row.adjust_type,
        state_code=row.state_code,
        parameter_key=row.parameter_key,
        indicator_type=row.indicator_type,
        parameters=dict(row.parameters),
        status=row.status,
        trade_date=row.trade_date,
        since_date=row.since_date,
        observed_from=row.observed_from or row.trade_date,
        since_is_exact=row.since_is_exact,
        source_revision=row.source_revision or 0,
        metadata_json=dict(row.metadata_json),
        updated_at=datetime.now(UTC),
    )
    session.add(model)
    return model
