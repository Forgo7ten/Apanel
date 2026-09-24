"""Persistence queries for public securities and market data."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyBar, IndicatorSnapshot, IndicatorState, QuoteSnapshot, Security


class SecurityRepository:
    """Read-only repository for shared market data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, query: str, *, limit: int = 50) -> list[Security]:
        pattern = f"%{query}%"
        statement = (
            select(Security)
            .where(or_(Security.symbol.ilike(pattern), Security.name.ilike(pattern)))
            .order_by(Security.symbol.asc())
            .limit(limit)
        )
        return list((await self.session.execute(statement)).scalars())

    async def get_by_symbol(self, symbol: str) -> Security | None:
        return (
            await self.session.execute(select(Security).where(Security.symbol == symbol))
        ).scalar_one_or_none()

    async def get_by_id(self, security_id: int) -> Security | None:
        return await self.session.get(Security, security_id)

    async def latest_quote(self, security_id: int) -> QuoteSnapshot | None:
        return (
            await self.session.execute(
                select(QuoteSnapshot)
                .where(QuoteSnapshot.security_id == security_id)
                .order_by(QuoteSnapshot.timestamp.desc(), QuoteSnapshot.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def daily_bars(
        self,
        security_id: int,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str | None = None,
    ) -> list[DailyBar]:
        statement: Select[tuple[DailyBar]] = select(DailyBar).where(
            DailyBar.security_id == security_id
        )
        if start_date is not None:
            statement = statement.where(DailyBar.trade_date >= start_date)
        if end_date is not None:
            statement = statement.where(DailyBar.trade_date <= end_date)
        if adjust_type is not None:
            statement = statement.where(DailyBar.adjust_type == adjust_type)
        statement = statement.order_by(DailyBar.trade_date.asc(), DailyBar.id.asc())
        return list((await self.session.execute(statement)).scalars())

    async def latest_indicators(self, security_id: int) -> list[IndicatorSnapshot]:
        latest_date = (
            await self.session.execute(
                select(IndicatorSnapshot.trade_date)
                .where(IndicatorSnapshot.security_id == security_id)
                .order_by(IndicatorSnapshot.trade_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_date is None:
            return []
        return list(
            (
                await self.session.execute(
                    select(IndicatorSnapshot)
                    .where(
                        IndicatorSnapshot.security_id == security_id,
                        IndicatorSnapshot.trade_date == latest_date,
                    )
                    .order_by(IndicatorSnapshot.indicator_type.asc())
                )
            ).scalars()
        )

    async def current_states(self, security_id: int) -> list[IndicatorState]:
        latest_date = (
            await self.session.execute(
                select(IndicatorState.trade_date)
                .where(IndicatorState.security_id == security_id)
                .order_by(IndicatorState.trade_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_date is None:
            return []
        return list(
            (
                await self.session.execute(
                    select(IndicatorState)
                    .where(
                        IndicatorState.security_id == security_id,
                        IndicatorState.trade_date == latest_date,
                        IndicatorState.status == "ACTIVE",
                    )
                    .order_by(IndicatorState.id.asc())
                )
            ).scalars()
        )


__all__ = ["SecurityRepository"]
