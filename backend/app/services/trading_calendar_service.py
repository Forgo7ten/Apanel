"""Trading-day readiness from the persisted official annual calendar."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from datetime import date, timedelta

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

    async def previous_open_dates(
        self,
        trade_dates: Iterable[date],
        *,
        market: str = "CN",
    ) -> dict[date, date | None]:
        """Resolve the immediately previous confirmed trading day for each date.

        ``None`` is fail-closed: it means the calendar cannot prove continuity
        for that date because the target is not confirmed OPEN, no earlier OPEN
        day is available, or an intervening calendar date is missing/UNKNOWN.
        """

        targets = tuple(sorted(set(trade_dates)))
        if not targets:
            return {}

        rows = list(
            (
                await self.session.execute(
                    select(TradingCalendar)
                    .where(
                        TradingCalendar.market == market,
                        TradingCalendar.trade_date <= targets[-1],
                    )
                    .order_by(TradingCalendar.trade_date.asc())
                )
            ).scalars()
        )
        by_date = {row.trade_date: row for row in rows}
        open_dates = tuple(row.trade_date for row in rows if row.status == "OPEN")
        result: dict[date, date | None] = {}

        for target in targets:
            target_row = by_date.get(target)
            if target_row is None or target_row.status != "OPEN":
                result[target] = None
                continue

            index = bisect_left(open_dates, target)
            if index == 0:
                result[target] = None
                continue
            previous = open_dates[index - 1]
            current = previous + timedelta(days=1)
            complete = True
            while current < target:
                row = by_date.get(current)
                if row is None or row.status != "CLOSED":
                    complete = False
                    break
                current += timedelta(days=1)
            result[target] = previous if complete else None

        return result
