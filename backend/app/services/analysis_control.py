"""Durable stream locks and demand invalidation shared by application services."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalysisStream, User


def insert_for(session: AsyncSession, model):
    factory = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    return factory(model)


async def lock_analysis_stream(
    session: AsyncSession, security_id: int, adjustment: str
) -> AnalysisStream:
    await session.execute(
        insert_for(session, AnalysisStream)
        .values(
            security_id=security_id,
            adjust_type=adjustment,
        )
        .on_conflict_do_nothing()
    )
    return (
        await session.execute(
            select(AnalysisStream)
            .where(
                AnalysisStream.security_id == security_id,
                AnalysisStream.adjust_type == adjustment,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def mark_demand_changed(session: AsyncSession, security_ids: Iterable[int]) -> None:
    keys = sorted({(int(sid), mode) for sid in security_ids for mode in ("none", "qfq")})
    if not keys:
        return
    statement = insert_for(session, AnalysisStream).values(
        [{"security_id": sid, "adjust_type": mode, "demand_generation": 1} for sid, mode in keys]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["security_id", "adjust_type"],
            set_={
                "demand_generation": AnalysisStream.demand_generation + 1,
                "updated_at": datetime.now(UTC),
            },
        )
    )


async def lock_account(session: AsyncSession, user_id: int) -> None:
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def lock_analysis_reads(
    session: AsyncSession, security_ids: Iterable[int], adjustment: str
) -> dict[int, AnalysisStream]:
    """Read-only share lock avoids mixing snapshots from different publication commits.

    No rows are created by HTTP reads. Writers acquire the same rows before
    modifying facts or observations; they never call a provider while holding them.
    """
    ids = sorted(set(security_ids))
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(AnalysisStream)
                .where(
                    tuple_(AnalysisStream.security_id, AnalysisStream.adjust_type).in_(
                        [(sid, adjustment) for sid in ids]
                    )
                )
                .order_by(AnalysisStream.security_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {row.security_id: row for row in rows}


async def ensure_monitor_capacity(
    session: AsyncSession, user_id: int, security_id: int, *, exclude_rule_id: int | None = None
) -> None:
    """Caller holds the account lock; distinct watch/active-rule targets share one limit."""
    from sqlalchemy import union

    from app.core.errors import ApiError
    from app.models import AlertRule, WatchTable, WatchTableSymbol

    watched = (
        select(WatchTableSymbol.security_id).join(WatchTable).where(WatchTable.user_id == user_id)
    )
    alerted = select(AlertRule.security_id).where(
        AlertRule.user_id == user_id, AlertRule.enabled.is_(True)
    )
    if exclude_rule_id is not None:
        alerted = alerted.where(AlertRule.id != exclude_rule_id)
    ids = set((await session.execute(union(watched, alerted))).scalars())
    if security_id not in ids and len(ids) >= 100:
        raise ApiError(
            "MONITOR_LIMIT_EXCEEDED", "At most 100 distinct securities may be monitored.", 409
        )


def publication_is_current(stream: AnalysisStream | None) -> bool:
    if stream is None or (stream.source_revision == 0 and stream.published_revision < 0):
        # New/offline callers may not have a Hub source watermark yet.
        return True
    return (
        stream.dirty_from is None
        and stream.published_revision == stream.source_revision
        and stream.published_demand_generation == stream.demand_generation
    )


async def published_stream_for_read(session: AsyncSession, security_id: int, adjustment: str):
    return (
        await session.execute(
            select(AnalysisStream)
            .where(
                AnalysisStream.security_id == security_id,
                AnalysisStream.adjust_type == adjustment,
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
