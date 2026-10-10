"""Shared stream control: short transactions, no provider/network calls under a lock."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert

from app.models.market_data import AnalysisStreamModel


async def lock_streams(session, keys):
    keys = sorted(set(keys))
    if not keys:
        return {}
    from sqlalchemy import tuple_

    await session.execute(
        insert(AnalysisStreamModel)
        .values([{"security_id": sid, "adjust_type": adjustment} for sid, adjustment in keys])
        .on_conflict_do_nothing()
    )
    rows = (
        (
            await session.execute(
                select(AnalysisStreamModel)
                .where(
                    tuple_(AnalysisStreamModel.security_id, AnalysisStreamModel.adjust_type).in_(
                        keys
                    )
                )
                .order_by(AnalysisStreamModel.security_id, AnalysisStreamModel.adjust_type)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {(row.security_id, row.adjust_type): row for row in rows}


async def invalidate_changed(session, changed: dict, latest: dict) -> None:
    """Persist earliest dirty date in the same transaction as changed market facts."""
    if not changed:
        return
    table = AnalysisStreamModel.__table__
    values = [
        {
            "security_id": sid,
            "adjust_type": adjust,
            "source_revision": 1,
            "dirty_from": first,
            "latest_bar_date": latest[(sid, adjust)],
            "updated_at": datetime.now(UTC),
        }
        for (sid, adjust), first in sorted(changed.items())
    ]
    statement = insert(table).values(values)
    incoming = statement.excluded
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["security_id", "adjust_type"],
            set_={
                "source_revision": table.c.source_revision + 1,
                "dirty_from": case(
                    (table.c.dirty_from.is_(None), incoming.dirty_from),
                    (table.c.dirty_from > incoming.dirty_from, incoming.dirty_from),
                    else_=table.c.dirty_from,
                ),
                "latest_bar_date": case(
                    (table.c.latest_bar_date.is_(None), incoming.latest_bar_date),
                    (table.c.latest_bar_date < incoming.latest_bar_date, incoming.latest_bar_date),
                    else_=table.c.latest_bar_date,
                ),
                "updated_at": incoming.updated_at,
            },
        )
    )


def combine_changed(target: dict, key: tuple[int, str], day: date) -> None:
    target[key] = min(target.get(key, day), day)
