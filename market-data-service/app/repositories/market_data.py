"""Async SQLAlchemy repositories for the market-data domain.

Each public write method opens its own session and transaction.  PostgreSQL
``ON CONFLICT`` statements make retries idempotent while keeping the unique
keys explicit at this boundary.  SQLAlchemy exceptions are deliberately not
swallowed here: the sync layer can isolate record-level failures but must
surface connection and transaction failures to its caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.market_data import (
    Adjustment,
    DailyBar,
    Dividend,
    Quote,
    Security,
    normalize_adjustment,
    normalize_symbol,
)
from app.models.market_data import (
    DailyBarModel,
    DividendEventModel,
    QuoteSnapshotModel,
    SecurityModel,
)


class PersistenceRecordError(RuntimeError):
    """A record cannot be persisted without indicating a system outage."""


class PersistenceSystemError(RuntimeError):
    """A database outage that must not be reduced to a symbol failure."""


class MissingSecurityError(PersistenceRecordError):
    """A child market-data record references no persisted security."""


class SecurityRepository(Protocol):
    """Repository seam consumed by security synchronization services."""

    async def upsert_many(self, records: Sequence[Security]) -> None: ...


class DailyBarRepository(Protocol):
    """Repository seam consumed by daily-bar synchronization services."""

    async def upsert_many(self, records: Sequence[DailyBar]) -> None: ...

    async def list_by_symbol(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: Adjustment | str = Adjustment.QFQ,
    ) -> tuple[DailyBar, ...]: ...


class QuoteRepository(Protocol):
    """Repository seam consumed by quote API reads and quote writers."""

    async def get_latest(self, symbol: str) -> Quote | None: ...


class DividendRepository(Protocol):
    """Repository seam consumed by dividend writers."""

    async def upsert_many(self, records: Sequence[Dividend]) -> None: ...


class SqlAlchemySecurityRepository:
    """Protocol-compatible repository for security metadata."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def build_upsert_statement(records: Sequence[Security]):
        values = [_security_values(record) for record in records]
        statement = postgres_insert(SecurityModel).values(values)
        return statement.on_conflict_do_update(
            index_elements=[SecurityModel.symbol],
            set_={
                "name": statement.excluded.name,
                "market": statement.excluded.market,
                "exchange": statement.excluded.exchange,
                "security_type": statement.excluded.security_type,
                "status": statement.excluded.status,
            },
        )

    async def upsert_many(self, records: Sequence[Security]) -> None:
        records = tuple(records)
        if not records:
            return
        if any(not isinstance(record, Security) for record in records):
            raise PersistenceRecordError("security repository received an invalid record")
        records = _unique_records(records, key=lambda record: record.symbol)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(self.build_upsert_statement(records))

    async def get_by_symbol(self, symbol: str) -> Security | None:
        canonical_symbol = normalize_symbol(symbol)
        async with self._session_factory() as session:
            result = await session.execute(
                select(SecurityModel).where(SecurityModel.symbol == canonical_symbol)
            )
            row = result.scalar_one_or_none()
        return None if row is None else _security_from_model(row)


class SqlAlchemyDailyBarRepository:
    """Protocol-compatible repository for adjusted daily bars."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def build_upsert_statement(records: Sequence[dict[str, Any]]):
        values = [_daily_values(item["security_id"], item["record"]) for item in records]
        statement = postgres_insert(DailyBarModel).values(values)
        return statement.on_conflict_do_update(
            index_elements=[
                DailyBarModel.security_id,
                DailyBarModel.trade_date,
                DailyBarModel.adjust_type,
            ],
            set_={
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "amount": statement.excluded.amount,
            },
        )

    async def upsert_many(self, records: Sequence[DailyBar]) -> None:
        records = tuple(records)
        if not records:
            return
        if any(not isinstance(record, DailyBar) for record in records):
            raise PersistenceRecordError("daily-bar repository received an invalid record")
        records = _unique_records(
            records,
            key=lambda record: (record.symbol, record.trade_date, record.adjustment),
        )
        symbols = tuple(dict.fromkeys(record.symbol for record in records))
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, symbols)
                await session.execute(
                    self.build_upsert_statement(
                        [
                            {"security_id": security_ids[record.symbol], "record": record}
                            for record in records
                        ]
                    )
                )

    async def list_by_symbol(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: Adjustment | str = Adjustment.QFQ,
    ) -> tuple[DailyBar, ...]:
        canonical_symbol = normalize_symbol(symbol)
        selected_adjustment = normalize_adjustment(adjustment)
        statement = (
            select(DailyBarModel, SecurityModel.symbol)
            .join(SecurityModel, DailyBarModel.security_id == SecurityModel.id)
            .where(
                SecurityModel.symbol == canonical_symbol,
                DailyBarModel.adjust_type == selected_adjustment.value,
            )
            .order_by(DailyBarModel.trade_date)
        )
        if start is not None:
            statement = statement.where(DailyBarModel.trade_date >= start)
        if end is not None:
            statement = statement.where(DailyBarModel.trade_date <= end)
        async with self._session_factory() as session:
            result = await session.execute(statement)
            rows = result.all()
        return tuple(_daily_from_model(row[0], row[1]) for row in rows)


class SqlAlchemyQuoteSnapshotRepository:
    """Repository for UTC quote snapshots and latest-quote reads."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def build_upsert_statement(records: Sequence[dict[str, Any]]):
        values = [_quote_values(item["security_id"], item["record"]) for item in records]
        statement = postgres_insert(QuoteSnapshotModel).values(values)
        return statement.on_conflict_do_update(
            index_elements=[QuoteSnapshotModel.security_id, QuoteSnapshotModel.timestamp],
            set_={
                "price": statement.excluded.price,
                "change": statement.excluded.change,
                "change_percent": statement.excluded.change_percent,
            },
        )

    async def upsert_many(self, records: Sequence[Quote]) -> None:
        records = tuple(records)
        if not records:
            return
        if any(not isinstance(record, Quote) for record in records):
            raise PersistenceRecordError("quote repository received an invalid record")
        records = _unique_records(records, key=lambda record: (record.symbol, record.timestamp))
        symbols = tuple(dict.fromkeys(record.symbol for record in records))
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, symbols)
                await session.execute(
                    self.build_upsert_statement(
                        [
                            {"security_id": security_ids[record.symbol], "record": record}
                            for record in records
                        ]
                    )
                )

    async def get_latest(self, symbol: str) -> Quote | None:
        canonical_symbol = normalize_symbol(symbol)
        statement = (
            select(QuoteSnapshotModel, SecurityModel.symbol)
            .join(SecurityModel, QuoteSnapshotModel.security_id == SecurityModel.id)
            .where(SecurityModel.symbol == canonical_symbol)
            .order_by(QuoteSnapshotModel.timestamp.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            row = result.first()
        return None if row is None else _quote_from_model(row[0], row[1])


class SqlAlchemyDividendEventRepository:
    """Repository for cash dividend events."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def build_upsert_statement(records: Sequence[dict[str, Any]]):
        values = [_dividend_values(item["security_id"], item["record"]) for item in records]
        statement = postgres_insert(DividendEventModel).values(values)
        return statement.on_conflict_do_update(
            index_elements=[DividendEventModel.security_id, DividendEventModel.date],
            set_={"cash_amount": statement.excluded.cash_amount},
        )

    async def upsert_many(self, records: Sequence[Dividend]) -> None:
        records = tuple(records)
        if not records:
            return
        if any(not isinstance(record, Dividend) for record in records):
            raise PersistenceRecordError("dividend repository received an invalid record")
        records = _unique_records(records, key=lambda record: (record.symbol, record.date))
        symbols = tuple(dict.fromkeys(record.symbol for record in records))
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, symbols)
                await session.execute(
                    self.build_upsert_statement(
                        [
                            {"security_id": security_ids[record.symbol], "record": record}
                            for record in records
                        ]
                    )
                )


async def _security_ids(session: AsyncSession, symbols: Sequence[str]) -> dict[str, int]:
    result = await session.execute(
        select(SecurityModel.id, SecurityModel.symbol).where(SecurityModel.symbol.in_(symbols))
    )
    found = {row[1]: row[0] for row in result.all()}
    missing = [symbol for symbol in symbols if symbol not in found]
    if missing:
        joined = ", ".join(missing)
        raise MissingSecurityError(f"security is not persisted: {joined}")
    return found


def _security_values(record: Security) -> dict[str, Any]:
    return {
        "symbol": record.symbol,
        "name": record.name,
        "market": record.market.value,
        "exchange": record.exchange,
        "security_type": record.security_type.value,
        "status": "ACTIVE",
    }


def _daily_values(security_id: int, record: DailyBar) -> dict[str, Any]:
    return {
        "security_id": security_id,
        "trade_date": record.trade_date,
        "open": record.open,
        "high": record.high,
        "low": record.low,
        "close": record.close,
        "volume": record.volume,
        "amount": record.amount,
        "adjust_type": record.adjustment.value,
    }


def _quote_values(security_id: int, record: Quote) -> dict[str, Any]:
    return {
        "security_id": security_id,
        "price": record.price,
        "change": record.change,
        "change_percent": record.change_percent,
        "timestamp": record.timestamp.astimezone(UTC),
    }


def _dividend_values(security_id: int, record: Dividend) -> dict[str, Any]:
    return {"security_id": security_id, "date": record.date, "cash_amount": record.cash_amount}


def _unique_records(records: Sequence[Any], *, key) -> tuple[Any, ...]:
    """Keep the last value for a unique key before a PostgreSQL upsert."""

    unique: dict[Any, Any] = {}
    for record in records:
        unique[key(record)] = record
    return tuple(unique.values())


def _security_from_model(model: SecurityModel) -> Security:
    return Security(
        symbol=model.symbol,
        name=model.name,
        market=model.market,
        exchange=model.exchange,
        security_type=model.security_type,
    )


def _daily_from_model(model: DailyBarModel, symbol: str) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=model.trade_date,
        open=model.open,
        high=model.high,
        low=model.low,
        close=model.close,
        volume=model.volume,
        amount=model.amount,
        adjustment=model.adjust_type,
    )


def _quote_from_model(model: QuoteSnapshotModel, symbol: str) -> Quote:
    timestamp = model.timestamp
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return Quote(
        symbol=symbol,
        price=model.price,
        change=model.change,
        change_percent=model.change_percent,
        timestamp=timestamp.astimezone(UTC),
    )


__all__ = [
    "DailyBarRepository",
    "DividendRepository",
    "MissingSecurityError",
    "PersistenceRecordError",
    "PersistenceSystemError",
    "QuoteRepository",
    "SecurityRepository",
    "SqlAlchemyDailyBarRepository",
    "SqlAlchemyDividendEventRepository",
    "SqlAlchemyQuoteSnapshotRepository",
    "SqlAlchemySecurityRepository",
]
