"""Persistence queries for user-owned alert rules and edge state."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import AlertInstance, AlertRule, IndicatorSnapshot, IndicatorState, Notification


class AlertRepository:
    """All rule queries carry the authenticated user id at the boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: int) -> list[AlertRule]:
        statement = (
            select(AlertRule)
            .where(AlertRule.user_id == user_id)
            .options(joinedload(AlertRule.security))
            .order_by(AlertRule.created_at.desc(), AlertRule.id.desc())
        )
        return list((await self.session.execute(statement)).scalars())

    async def get_owned(self, rule_id: int, user_id: int) -> AlertRule | None:
        statement = (
            select(AlertRule)
            .where(AlertRule.id == rule_id, AlertRule.user_id == user_id)
            .options(joinedload(AlertRule.security))
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_for_evaluation(self, rule_id: int) -> AlertRule | None:
        """Lock one rule while calculating and claiming its next edge.

        The rule row is the parent isolation boundary for ``AlertInstance``.
        Locking it also serializes the first evaluation when no instance row
        exists yet, avoiding a duplicate trigger from concurrent schedulers.
        """

        statement = (
            select(AlertRule)
            .where(AlertRule.id == rule_id)
            .options(joinedload(AlertRule.security))
            .with_for_update(of=AlertRule)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_enabled_for_evaluation(self) -> list[AlertRule]:
        statement = (
            select(AlertRule)
            .where(AlertRule.enabled.is_(True))
            .options(joinedload(AlertRule.security))
            .order_by(AlertRule.user_id.asc(), AlertRule.id.asc())
        )
        return list((await self.session.execute(statement)).scalars())

    async def get_instance(
        self, alert_rule_id: int, security_id: int
    ) -> AlertInstance | None:
        return (
            await self.session.execute(
                select(AlertInstance).where(
                    AlertInstance.alert_rule_id == alert_rule_id,
                    AlertInstance.security_id == security_id,
                )
            )
        ).scalar_one_or_none()

    async def latest_snapshot(
        self, security_id: int, indicator_type: str
    ) -> IndicatorSnapshot | None:
        return (
            await self.session.execute(
                select(IndicatorSnapshot)
                .where(
                    IndicatorSnapshot.security_id == security_id,
                    IndicatorSnapshot.indicator_type == indicator_type,
                )
                .order_by(IndicatorSnapshot.trade_date.desc(), IndicatorSnapshot.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def latest_snapshots(self, security_id: int) -> list[IndicatorSnapshot]:
        latest_date = (
            await self.session.execute(
                select(func.max(IndicatorSnapshot.trade_date)).where(
                    IndicatorSnapshot.security_id == security_id
                )
            )
        ).scalar_one_or_none()
        if latest_date is None:
            return []
        statement: Select[tuple[IndicatorSnapshot]] = (
            select(IndicatorSnapshot)
            .where(
                IndicatorSnapshot.security_id == security_id,
                IndicatorSnapshot.trade_date == latest_date,
            )
            .order_by(IndicatorSnapshot.indicator_type.asc())
        )
        return list((await self.session.execute(statement)).scalars())

    async def latest_states(self, security_id: int) -> list[IndicatorState]:
        latest_date = (
            await self.session.execute(
                select(func.max(IndicatorState.trade_date)).where(
                    IndicatorState.security_id == security_id
                )
            )
        ).scalar_one_or_none()
        if latest_date is None:
            return []
        statement: Select[tuple[IndicatorState]] = select(IndicatorState).where(
            IndicatorState.security_id == security_id,
            IndicatorState.trade_date == latest_date,
        )
        return list((await self.session.execute(statement)).scalars())

    async def latest_observation_date(self, security_id: int) -> date | None:
        snapshot_date = (
            await self.session.execute(
                select(func.max(IndicatorSnapshot.trade_date)).where(
                    IndicatorSnapshot.security_id == security_id
                )
            )
        ).scalar_one_or_none()
        state_date = (
            await self.session.execute(
                select(func.max(IndicatorState.trade_date)).where(
                    IndicatorState.security_id == security_id
                )
            )
        ).scalar_one_or_none()
        if snapshot_date is None:
            return state_date
        if state_date is None:
            return snapshot_date
        return max(snapshot_date, state_date)

    async def list_notifications(self, user_id: int, *, limit: int = 100) -> list:
        statement = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .options(
                joinedload(Notification.security),
                joinedload(Notification.alert_rule),
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
        return list((await self.session.execute(statement)).scalars())


__all__ = ["AlertRepository"]
