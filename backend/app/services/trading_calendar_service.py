"""Trading-day readiness from the persisted official annual calendar."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TradingCalendar


class TradingCalendarUnavailable(RuntimeError):
    pass


class TradingCalendarService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def status(self, trade_date: date, *, market: str = "CN") -> str:
        row = (
            await self.session.execute(
                select(TradingCalendar).where(
                    TradingCalendar.market == market,
                    TradingCalendar.trade_date == trade_date,
                )
            )
        ).scalar_one_or_none()
        if row is None or row.status == "UNKNOWN":
            raise TradingCalendarUnavailable("trading calendar is unavailable")
        return row.status

    async def is_open(self, trade_date: date, *, market: str = "CN") -> bool:
        return await self.status(trade_date, market=market) == "OPEN"
