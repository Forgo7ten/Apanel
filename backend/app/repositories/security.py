"""Persistence queries for public securities and market data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DATE,
    INTEGER,
    NUMERIC,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Select,
    Table,
    UniqueConstraint,
    and_,
    case,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.models import DailyBar, IndicatorSnapshot, IndicatorState, QuoteSnapshot, Security

_dividend_events_table = Base.metadata.tables.get("dividend_events")
if _dividend_events_table is None:
    # The table is created by the market-data migration and owned by the
    # market-data service.  The backend only needs this Core read projection;
    # deliberately do not add a second ORM model or a migration here.
    _dividend_events_table = Table(
        "dividend_events",
        Base.metadata,
        Column("id", INTEGER, primary_key=True, autoincrement=True),
        Column(
            "security_id",
            INTEGER,
            ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("date", DATE, nullable=False),
        Column("cash_amount", NUMERIC(asdecimal=True), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("security_id", "date", name="uq_dividend_events_security_date"),
        Index("ix_dividend_events_security_date", "security_id", "date"),
    )

DIVIDEND_EVENTS_TABLE = _dividend_events_table


@dataclass(frozen=True, slots=True)
class DividendEventRecord:
    """Read-only projection of one persisted cash dividend event."""

    id: int
    security_id: int
    date: date
    cash_amount: Decimal


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

    async def latest_quotes_batch(
        self, security_ids: Iterable[int]
    ) -> dict[int, QuoteSnapshot]:
        """Return one newest quote per requested security in one query."""

        ids = _unique_ids(security_ids)
        if not ids:
            return {}
        ranked = (
            select(
                QuoteSnapshot.id.label("quote_id"),
                func.row_number()
                .over(
                    partition_by=QuoteSnapshot.security_id,
                    order_by=(QuoteSnapshot.timestamp.desc(), QuoteSnapshot.id.desc()),
                )
                .label("row_number"),
            )
            .where(QuoteSnapshot.security_id.in_(ids))
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(QuoteSnapshot)
                .join(ranked, QuoteSnapshot.id == ranked.c.quote_id)
                .where(ranked.c.row_number == 1)
            )
        ).scalars()
        return {row.security_id: row for row in rows}

    async def latest_daily_bar(self, security_id: int) -> DailyBar | None:
        """Return the latest close, preferring an unadjusted bar on ties.

        A quote is the authoritative current price.  This read exists only as
        an explainable fallback for consumers such as TTM dividend yield when
        no quote snapshot has been persisted yet.
        """

        return (
            await self.session.execute(
                select(DailyBar)
                .where(DailyBar.security_id == security_id)
                .order_by(
                    DailyBar.trade_date.desc(),
                    case((DailyBar.adjust_type == "none", 0), else_=1),
                    DailyBar.id.desc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    async def latest_daily_bars_batch(
        self, security_ids: Iterable[int]
    ) -> dict[int, DailyBar]:
        """Return one newest close per requested security in one query."""

        ids = _unique_ids(security_ids)
        if not ids:
            return {}
        ranked = (
            select(
                DailyBar.id.label("bar_id"),
                func.row_number()
                .over(
                    partition_by=DailyBar.security_id,
                    order_by=(
                        DailyBar.trade_date.desc(),
                        case((DailyBar.adjust_type == "none", 0), else_=1),
                        DailyBar.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .where(DailyBar.security_id.in_(ids))
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(DailyBar)
                .join(ranked, DailyBar.id == ranked.c.bar_id)
                .where(ranked.c.row_number == 1)
            )
        ).scalars()
        return {row.security_id: row for row in rows}

    async def dividend_events(
        self,
        security_id: int,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[DividendEventRecord]:
        """Return cash dividend events in chronological order.

        ``dividend_events`` is maintained by the market-data service.  Using
        a Core table projection keeps the backend read-only and avoids adding
        a competing ORM/Alembic definition for that shared table.
        """

        statement = select(
            DIVIDEND_EVENTS_TABLE.c.id,
            DIVIDEND_EVENTS_TABLE.c.security_id,
            DIVIDEND_EVENTS_TABLE.c.date,
            DIVIDEND_EVENTS_TABLE.c.cash_amount,
        ).where(DIVIDEND_EVENTS_TABLE.c.security_id == security_id)
        if start_date is not None:
            statement = statement.where(DIVIDEND_EVENTS_TABLE.c.date >= start_date)
        if end_date is not None:
            statement = statement.where(DIVIDEND_EVENTS_TABLE.c.date <= end_date)
        statement = statement.order_by(
            DIVIDEND_EVENTS_TABLE.c.date.asc(), DIVIDEND_EVENTS_TABLE.c.id.asc()
        )
        rows = (await self.session.execute(statement)).mappings().all()
        return [
            DividendEventRecord(
                id=int(row["id"]),
                security_id=int(row["security_id"]),
                date=row["date"],
                cash_amount=_decimal(row["cash_amount"]),
            )
            for row in rows
        ]

    async def dividend_events_batch(
        self,
        security_ids: Iterable[int],
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[int, list[DividendEventRecord]]:
        """Return dividend events for all requested securities in one query."""

        ids = _unique_ids(security_ids)
        result = {security_id: [] for security_id in ids}
        if not ids:
            return result
        statement = select(
            DIVIDEND_EVENTS_TABLE.c.id,
            DIVIDEND_EVENTS_TABLE.c.security_id,
            DIVIDEND_EVENTS_TABLE.c.date,
            DIVIDEND_EVENTS_TABLE.c.cash_amount,
        ).where(DIVIDEND_EVENTS_TABLE.c.security_id.in_(ids))
        if start_date is not None:
            statement = statement.where(DIVIDEND_EVENTS_TABLE.c.date >= start_date)
        if end_date is not None:
            statement = statement.where(DIVIDEND_EVENTS_TABLE.c.date <= end_date)
        statement = statement.order_by(
            DIVIDEND_EVENTS_TABLE.c.security_id.asc(),
            DIVIDEND_EVENTS_TABLE.c.date.asc(),
            DIVIDEND_EVENTS_TABLE.c.id.asc(),
        )
        rows = (await self.session.execute(statement)).mappings().all()
        for row in rows:
            event = DividendEventRecord(
                id=int(row["id"]),
                security_id=int(row["security_id"]),
                date=row["date"],
                cash_amount=_decimal(row["cash_amount"]),
            )
            result[event.security_id].append(event)
        return result

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

    async def latest_indicators_batch(
        self, security_ids: Iterable[int]
    ) -> dict[int, list[IndicatorSnapshot]]:
        """Return all snapshots from each requested security's newest date."""

        ids = _unique_ids(security_ids)
        result = {security_id: [] for security_id in ids}
        if not ids:
            return result
        latest_dates = (
            select(
                IndicatorSnapshot.security_id,
                func.max(IndicatorSnapshot.trade_date).label("latest_date"),
            )
            .where(IndicatorSnapshot.security_id.in_(ids))
            .group_by(IndicatorSnapshot.security_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(IndicatorSnapshot)
                .join(
                    latest_dates,
                    and_(
                        IndicatorSnapshot.security_id == latest_dates.c.security_id,
                        IndicatorSnapshot.trade_date == latest_dates.c.latest_date,
                    ),
                )
                .order_by(
                    IndicatorSnapshot.security_id.asc(),
                    IndicatorSnapshot.indicator_type.asc(),
                    IndicatorSnapshot.id.asc(),
                )
            )
        ).scalars()
        for row in rows:
            result[row.security_id].append(row)
        return result

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

    async def current_states_batch(
        self, security_ids: Iterable[int]
    ) -> dict[int, list[IndicatorState]]:
        """Return active states from each requested security's newest date."""

        ids = _unique_ids(security_ids)
        result = {security_id: [] for security_id in ids}
        if not ids:
            return result
        latest_dates = (
            select(
                IndicatorState.security_id,
                func.max(IndicatorState.trade_date).label("latest_date"),
            )
            .where(IndicatorState.security_id.in_(ids))
            .group_by(IndicatorState.security_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(IndicatorState)
                .join(
                    latest_dates,
                    and_(
                        IndicatorState.security_id == latest_dates.c.security_id,
                        IndicatorState.trade_date == latest_dates.c.latest_date,
                    ),
                )
                .where(IndicatorState.status == "ACTIVE")
                .order_by(IndicatorState.security_id.asc(), IndicatorState.id.asc())
            )
        ).scalars()
        for row in rows:
            result[row.security_id].append(row)
        return result


def _decimal(value: Any) -> Decimal:
    """Normalize database numeric values without introducing float noise."""

    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _unique_ids(security_ids: Iterable[int]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(security_id) for security_id in security_ids))


__all__ = ["DIVIDEND_EVENTS_TABLE", "DividendEventRecord", "SecurityRepository"]
