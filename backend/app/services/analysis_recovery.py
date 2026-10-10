"""Bounded reconciliation of durable analysis demand, data and alert watermarks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select

from app.models import (
    AlertInstance,
    AlertRule,
    AnalysisStream,
    Security,
    TradingCalendar,
    User,
    UserStatus,
)


async def recovery_trade_date(session, settings, *, now=None):
    local = now or datetime.now(ZoneInfo(settings.timezone))
    cutoff = local.date()
    if (local.hour, local.minute) < (settings.eod_pipeline_hour, settings.eod_pipeline_minute):
        cutoff -= timedelta(days=1)
    return (
        await session.execute(
            select(func.max(TradingCalendar.trade_date)).where(
                TradingCalendar.market == "CN",
                TradingCalendar.status == "OPEN",
                TradingCalendar.trade_date <= cutoff,
            )
        )
    ).scalar_one_or_none()


async def pending_analysis_symbols(session, symbols, adjustments, target, *, limit=100):
    if not symbols:
        return ()
    rows = (
        await session.execute(
            select(Security.symbol, AnalysisStream)
            .join(AnalysisStream, AnalysisStream.security_id == Security.id)
            .where(Security.symbol.in_(symbols), AnalysisStream.adjust_type.in_(adjustments))
        )
    ).all()
    streams = {(symbol, stream.adjust_type): stream for symbol, stream in rows}
    pending = set()
    for symbol in symbols:
        for mode in adjustments:
            stream = streams.get((symbol, mode))
            if (
                stream is None
                or stream.dirty_from is not None
                or stream.published_revision != stream.source_revision
                or stream.published_demand_generation != stream.demand_generation
                or stream.state_through is None
            ):
                pending.add(symbol)
                continue
            last = stream.last_sync_at
            # A suspended security may legitimately have an old latest bar.
            # A successful target-day sync is sufficient; do not bootstrap it forever.
            if (
                last is None
                or (last if last.tzinfo else last.replace(tzinfo=UTC))
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                < target
            ):
                pending.add(symbol)
    # A process may have crashed after publishing analysis but before Alert/outbox commit.
    outstanding = (
        (
            await session.execute(
                select(Security.symbol)
                .join(AlertRule, AlertRule.security_id == Security.id)
                .join(User, User.id == AlertRule.user_id)
                .join(
                    AnalysisStream,
                    and_(
                        AnalysisStream.security_id == Security.id,
                        AnalysisStream.adjust_type == AlertRule.adjust_type,
                    ),
                )
                .outerjoin(
                    AlertInstance,
                    and_(
                        AlertInstance.alert_rule_id == AlertRule.id,
                        AlertInstance.security_id == Security.id,
                    ),
                )
                .where(
                    Security.symbol.in_(symbols),
                    AlertRule.enabled.is_(True),
                    User.status == UserStatus.ACTIVE,
                    AnalysisStream.state_through <= target,
                    or_(
                        AlertInstance.id.is_(None),
                        AlertInstance.last_evaluated_trade_date < AnalysisStream.state_through,
                        AlertInstance.rule_version != AlertRule.rule_version,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    pending.update(outstanding)
    # Bound each batch and process oldest synchronization first. Permanent
    # failures remain visible in pipeline issues for operator remediation.
    ordered = sorted(
        pending,
        key=lambda symbol: (
            min(
                (
                    str(stream.last_sync_at or "")
                    for (name, _), stream in streams.items()
                    if name == symbol
                ),
                default="",
            ),
            symbol,
        ),
    )
    return tuple(ordered[:limit])
