"""Async SQLAlchemy repositories for the market-data domain.

Each public write method opens its own session and transaction.  PostgreSQL
``ON CONFLICT`` statements make retries idempotent while keeping the unique
keys explicit at this boundary.  SQLAlchemy exceptions are deliberately not
swallowed here: the sync layer can isolate record-level failures but must
surface connection and transaction failures to its caller.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.deduplication import unique_facts
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
    MarketDataAdjustmentStateModel,
    QuoteSnapshotModel,
    SecurityModel,
    TradingCalendarModel,
)
from app.repositories.analysis_stream import combine_changed, invalidate_changed, lock_streams

_ASYNCPG_MAX_BIND_PARAMETERS = 32767


class PersistenceRecordError(RuntimeError):
    """A record cannot be persisted without indicating a system outage."""


class PersistenceSystemError(RuntimeError):
    """A database outage that must not be reduced to a symbol failure."""


class MissingSecurityError(PersistenceRecordError):
    """A child market-data record references no persisted security."""


class SecurityRepository(Protocol):
    """Repository seam consumed by security synchronization services."""

    async def upsert_many(self, records: Sequence[Security]) -> None: ...

    async def reconcile_snapshot(
        self, records: Sequence[Security], *, authoritative: bool = False
    ) -> None: ...


class DailyBarRepository(Protocol):
    """Repository seam consumed by daily-bar synchronization services."""

    async def upsert_many(self, records: Sequence[DailyBar]) -> None: ...

    async def reconcile_revision(
        self,
        symbol: str,
        records: Sequence[DailyBar],
        *,
        adjustment: Adjustment | str,
        coverage_start: date,
        coverage_end: date,
    ) -> None: ...

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

    async def upsert_many(self, records: Sequence[Quote]) -> None: ...

    async def get_latest(self, symbol: str) -> Quote | None: ...


class DividendRepository(Protocol):
    """Repository seam consumed by dividend synchronization and reads."""

    async def upsert_many(self, records: Sequence[Dividend]) -> None: ...

    async def list_by_symbol(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> tuple[Dividend, ...]: ...


class AdjustmentStateRepository(Protocol):
    async def get(self, symbol: str) -> dict[str, Any] | None: ...
    async def upsert(
        self,
        symbol: str,
        *,
        provider_name: str,
        revision_hash: str,
        coverage_start: date | None,
        coverage_end: date | None,
        rebased: bool,
    ) -> None: ...


class SqlAlchemyAdjustmentStateRepository:
    """Persist opaque qfq revision metadata owned by the Market Data Hub."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, symbol: str) -> dict[str, Any] | None:
        canonical = normalize_symbol(symbol)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(MarketDataAdjustmentStateModel, SecurityModel.symbol)
                    .join(
                        SecurityModel,
                        MarketDataAdjustmentStateModel.security_id == SecurityModel.id,
                    )
                    .where(SecurityModel.symbol == canonical)
                )
            ).first()
        if row is None:
            return None
        model = row[0]
        return {
            "revision_hash": model.revision_hash,
            "coverage_start": model.qfq_coverage_start,
            "coverage_end": model.qfq_coverage_end,
            "provider_name": model.provider_name,
        }

    async def upsert(
        self,
        symbol: str,
        *,
        provider_name: str,
        revision_hash: str,
        coverage_start: date | None,
        coverage_end: date | None,
        rebased: bool,
    ) -> None:
        canonical = normalize_symbol(symbol)
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, (canonical,))
                values = {
                    "security_id": security_ids[canonical],
                    "provider_name": provider_name,
                    "revision_hash": revision_hash,
                    "qfq_coverage_start": coverage_start,
                    "qfq_coverage_end": coverage_end,
                    "last_rebased_at": datetime.now(UTC) if rebased else None,
                }
                statement = postgres_insert(MarketDataAdjustmentStateModel).values(**values)
                update = {
                    "provider_name": statement.excluded.provider_name,
                    "revision_hash": statement.excluded.revision_hash,
                    "qfq_coverage_start": statement.excluded.qfq_coverage_start,
                    "qfq_coverage_end": statement.excluded.qfq_coverage_end,
                }
                if rebased:
                    update["last_rebased_at"] = statement.excluded.last_rebased_at
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[MarketDataAdjustmentStateModel.security_id],
                        set_=update,
                    )
                )


class SqlAlchemyTradingCalendarRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def validate(self, trade_date: date, *, actual_open: bool, source: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(
                    TradingCalendarModel, {"market": "CN", "trade_date": trade_date}
                )
                if row is None:
                    row = TradingCalendarModel(
                        market="CN",
                        trade_date=trade_date,
                        expected_open=None,
                        actual_open=actual_open,
                        status="OPEN" if actual_open else "CLOSED",
                        source=source,
                        source_metadata={},
                        validated_at=datetime.now(UTC),
                    )
                    session.add(row)
                else:
                    row.actual_open = actual_open
                    row.validated_at = datetime.now(UTC)
                    if row.expected_open is not None and row.expected_open != actual_open:
                        row.status = "UNKNOWN"
                    else:
                        row.status = "OPEN" if actual_open else "CLOSED"
                await session.flush()
                return {
                    "trade_date": row.trade_date,
                    "status": row.status,
                    "expected_open": row.expected_open,
                    "actual_open": row.actual_open,
                }


def validate_security_snapshot(previous, records: Sequence[Security]) -> None:
    """Fail closed on source truncation, including one missing exchange/category."""
    old = {symbol for symbol, _market, _kind in previous}
    current = {record.symbol for record in records}
    if old and len(old - current) / len(old) > 0.05:
        raise PersistenceRecordError("security snapshot shrinkage exceeds safety threshold")
    prior_segments = Counter((market, kind) for _symbol, market, kind in previous)
    new_segments = Counter((record.market.value, record.security_type.value) for record in records)
    if any(new_segments[segment] < count * 0.95 for segment, count in prior_segments.items()):
        raise PersistenceRecordError("security snapshot contains an incomplete market segment")


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
        records = self._validated_records(records)
        if not records:
            return
        async with self._session_factory() as session:
            async with session.begin():
                await self._upsert_in_session(session, records)

    async def reconcile_snapshot(
        self, records: Sequence[Security], *, authoritative: bool = False
    ) -> None:
        """Persist one authoritative universe and inactivate missing symbols atomically."""

        records = self._validated_records(records)
        if not records:
            raise PersistenceRecordError("security snapshot must not be empty")
        active_symbols = tuple(record.symbol for record in records)
        async with self._session_factory() as session:
            async with session.begin():
                bind = getattr(session, "bind", None)
                if bind is not None and bind.dialect.name == "postgresql":
                    await session.execute(text("SELECT pg_advisory_xact_lock(7284141301)"))
                previous = (
                    await session.execute(
                        select(
                            SecurityModel.symbol, SecurityModel.market, SecurityModel.security_type
                        ).where(SecurityModel.status == "ACTIVE")
                    )
                ).all()
                validate_security_snapshot(previous, records)
                await self._upsert_in_session(session, records)
                # Missing is not delisted. TDX/AKShare lists have no explicit
                # removal authority; only a verified full snapshot may remove.
                if authoritative:
                    await session.execute(
                        update(SecurityModel)
                        .where(
                            SecurityModel.status == "ACTIVE",
                            SecurityModel.symbol.not_in(active_symbols),
                        )
                        .values(status="INACTIVE")
                    )

    def _validated_records(self, records: Sequence[Security]) -> tuple[Security, ...]:
        records = tuple(records)
        if any(not isinstance(record, Security) for record in records):
            raise PersistenceRecordError("security repository received an invalid record")
        return _unique_records(records, key=lambda record: record.symbol)

    async def _upsert_in_session(self, session: AsyncSession, records: Sequence[Security]) -> None:
        parameters_per_record = len(_security_values(records[0]))
        batch_size = max(1, _ASYNCPG_MAX_BIND_PARAMETERS // parameters_per_record)
        # asyncpg rejects prepared statements with more than 32,767 bind
        # arguments. Keep reconciliation atomic while executing bounded SQL.
        for offset in range(0, len(records), batch_size):
            batch = records[offset : offset + batch_size]
            await session.execute(self.build_upsert_statement(batch))

    async def get_by_symbol(self, symbol: str) -> Security | None:
        canonical_symbol = normalize_symbol(symbol)
        async with self._session_factory() as session:
            result = await session.execute(
                select(SecurityModel).where(SecurityModel.symbol == canonical_symbol)
            )
            row = result.scalar_one_or_none()
        return None if row is None else _security_from_model(row)


class SqlAlchemyDailyBarRepository:
    """Atomic facts + invalidation, with fencing against stale provider responses."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def build_upsert_statement(records: Sequence[dict[str, Any]]):
        values = [_daily_values(item["security_id"], item["record"]) for item in records]
        statement = postgres_insert(DailyBarModel).values(values)
        fields = ("open", "high", "low", "close", "volume", "amount")
        return statement.on_conflict_do_update(
            index_elements=["security_id", "trade_date", "adjust_type"],
            set_={field: statement.excluded[field] for field in fields},
            where=or_(
                *(
                    getattr(DailyBarModel, field).is_distinct_from(statement.excluded[field])
                    for field in fields
                )
            ),
        ).returning(DailyBarModel.security_id, DailyBarModel.adjust_type, DailyBarModel.trade_date)

    async def reserve_sync(self, symbols, *, adjustment) -> dict[str, int]:
        selected = normalize_adjustment(adjustment).value
        normalized = sorted({normalize_symbol(symbol) for symbol in symbols})
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        select(SecurityModel.id, SecurityModel.symbol).where(
                            SecurityModel.symbol.in_(normalized)
                        )
                    )
                ).all()
                streams = await lock_streams(session, [(sid, selected) for sid, _symbol in rows])
                result = {}
                for sid, symbol in rows:
                    stream = streams[(sid, selected)]
                    stream.sync_generation += 1
                    result[symbol] = stream.sync_generation
                return result

    @staticmethod
    def _check_fences(streams, security_ids, expected, adjustment):
        if expected is None:
            return
        for symbol, security_id in security_ids.items():
            stream = streams[(security_id, adjustment)]
            if expected.get(symbol) != stream.sync_generation:
                raise PersistenceRecordError("market-data sync was superseded")

    async def upsert_many(self, records: Sequence[DailyBar]) -> None:
        await self.upsert_fenced(records)

    async def upsert_fenced(
        self,
        records: Sequence[DailyBar],
        *,
        generations=None,
        revisions=None,
        provider_name="provider",
    ) -> None:
        records = self._validated_records(records)
        if not records:
            return
        symbols = tuple(dict.fromkeys(record.symbol for record in records))
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, symbols)
                keys = {(security_ids[bar.symbol], bar.adjustment.value) for bar in records}
                streams = await lock_streams(session, keys)
                for adjustment in {bar.adjustment.value for bar in records}:
                    subset = {
                        symbol: sid
                        for symbol, sid in security_ids.items()
                        if (sid, adjustment) in keys
                    }
                    self._check_fences(streams, subset, generations, adjustment)
                changed, latest = await self._upsert_in_session(session, records, security_ids)
                if revisions:
                    for symbol, revision in revisions.items():
                        if symbol in security_ids and (security_ids[symbol], "qfq") in keys:
                            first, last, revised = await self._revision_in_session(
                                session, security_ids[symbol], revision, provider_name
                            )
                            if revised and first is not None:
                                combine_changed(changed, (security_ids[symbol], "qfq"), first)
                                latest[(security_ids[symbol], "qfq")] = last
                await invalidate_changed(session, changed, latest)

    async def reconcile_revision(
        self,
        symbol: str,
        records: Sequence[DailyBar],
        *,
        adjustment,
        coverage_start: date,
        coverage_end: date,
        generations=None,
        revision_hash=None,
        provider_name="provider",
    ) -> None:
        canonical = normalize_symbol(symbol)
        selected = normalize_adjustment(adjustment)
        records = self._validated_records(records)
        if not records or any(
            bar.symbol != canonical or bar.adjustment is not selected for bar in records
        ):
            raise PersistenceRecordError("revised snapshot does not match its target")
        if (
            coverage_start > coverage_end
            or min(bar.trade_date for bar in records) > coverage_start
            or max(bar.trade_date for bar in records) < coverage_end
        ):
            raise PersistenceRecordError("revised snapshot does not cover replacement range")
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, (canonical,))
                sid = security_ids[canonical]
                streams = await lock_streams(session, [(sid, selected.value)])
                self._check_fences(streams, security_ids, generations, selected.value)
                changed, latest = await self._upsert_in_session(session, records, security_ids)
                deleted = (
                    (
                        await session.execute(
                            delete(DailyBarModel)
                            .where(
                                DailyBarModel.security_id == sid,
                                DailyBarModel.adjust_type == selected.value,
                                DailyBarModel.trade_date >= coverage_start,
                                DailyBarModel.trade_date <= coverage_end,
                                DailyBarModel.trade_date.not_in(
                                    [bar.trade_date for bar in records]
                                ),
                            )
                            .returning(DailyBarModel.trade_date)
                        )
                    )
                    .scalars()
                    .all()
                )
                if deleted:
                    combine_changed(changed, (sid, selected.value), min(deleted))
                if revision_hash is not None:
                    await self._revision_in_session(
                        session, sid, revision_hash, provider_name, rebased=True
                    )
                combine_changed(changed, (sid, selected.value), coverage_start)
                latest[(sid, selected.value)] = max(bar.trade_date for bar in records)
                await invalidate_changed(session, changed, latest)

    async def _revision_in_session(
        self, session, security_id, revision, provider_name, rebased=False
    ):
        previous = (
            await session.execute(
                select(MarketDataAdjustmentStateModel.revision_hash).where(
                    MarketDataAdjustmentStateModel.security_id == security_id
                )
            )
        ).scalar_one_or_none()
        first, last = (
            await session.execute(
                select(
                    func.min(DailyBarModel.trade_date), func.max(DailyBarModel.trade_date)
                ).where(
                    DailyBarModel.security_id == security_id, DailyBarModel.adjust_type == "qfq"
                )
            )
        ).one()
        values = {
            "security_id": security_id,
            "provider_name": provider_name,
            "revision_hash": revision,
            "qfq_coverage_start": first,
            "qfq_coverage_end": last,
            "updated_at": datetime.now(UTC),
            "last_rebased_at": datetime.now(UTC) if rebased else None,
        }
        stmt = postgres_insert(MarketDataAdjustmentStateModel).values(**values)
        fields = (
            "provider_name",
            "revision_hash",
            "qfq_coverage_start",
            "qfq_coverage_end",
            "updated_at",
        )
        updates = {field: stmt.excluded[field] for field in fields}
        if rebased:
            updates["last_rebased_at"] = stmt.excluded.last_rebased_at
        await session.execute(
            stmt.on_conflict_do_update(index_elements=["security_id"], set_=updates)
        )
        return first, last, previous is not None and previous != revision

    async def complete_sync(self, symbols, *, adjustment, generations, history_depth=0):
        symbols = tuple(symbols)
        if not symbols:
            return
        selected = normalize_adjustment(adjustment).value
        async with self._session_factory() as session:
            async with session.begin():
                security_ids = await _security_ids(session, symbols)
                streams = await lock_streams(
                    session, [(sid, selected) for sid in security_ids.values()]
                )
                self._check_fences(streams, security_ids, generations, selected)
                for stream in streams.values():
                    stream.history_depth = max(stream.history_depth, history_depth or 0)
                    stream.last_sync_at = datetime.now(UTC)

    def _validated_records(self, records: Sequence[DailyBar]) -> tuple[DailyBar, ...]:
        records = tuple(records)
        if any(not isinstance(record, DailyBar) for record in records):
            raise PersistenceRecordError("daily-bar repository received an invalid record")
        unique = _unique_records(
            records, key=lambda bar: (bar.symbol, bar.trade_date, bar.adjustment)
        )
        return tuple(
            sorted(unique, key=lambda bar: (bar.symbol, bar.adjustment.value, bar.trade_date))
        )

    async def _upsert_in_session(self, session, records, security_ids):
        changed, latest = {}, {}
        size = len(_daily_values(security_ids[records[0].symbol], records[0]))
        batch_size = max(1, (_ASYNCPG_MAX_BIND_PARAMETERS - 64) // size)
        for offset in range(0, len(records), batch_size):
            statement = self.build_upsert_statement(
                [
                    {"security_id": security_ids[record.symbol], "record": record}
                    for record in records[offset : offset + batch_size]
                ]
            )
            rows = (await session.execute(statement)).all()
            for sid, adjustment, day in rows:
                combine_changed(changed, (sid, adjustment), day)
                latest[(sid, adjustment)] = max(latest.get((sid, adjustment), day), day)
        return changed, latest

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

    async def list_by_symbol(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> tuple[Dividend, ...]:
        """Return persisted cash-dividend events in chronological order."""

        canonical_symbol = normalize_symbol(symbol)
        statement = (
            select(DividendEventModel, SecurityModel.symbol)
            .join(SecurityModel, DividendEventModel.security_id == SecurityModel.id)
            .where(SecurityModel.symbol == canonical_symbol)
            .order_by(DividendEventModel.date)
        )
        if start is not None:
            statement = statement.where(DividendEventModel.date >= start)
        if end is not None:
            statement = statement.where(DividendEventModel.date <= end)
        async with self._session_factory() as session:
            result = await session.execute(statement)
            rows = result.all()
        return tuple(_dividend_from_model(row[0], row[1]) for row in rows)


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
    """Deduplicate identical facts, rejecting conflicts even for direct repository callers."""
    try:
        return unique_facts(records, key=key)
    except ValueError as error:
        raise PersistenceRecordError("conflicting market records for one identity") from error


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


def _dividend_from_model(model: DividendEventModel, symbol: str) -> Dividend:
    return Dividend(
        symbol=symbol,
        date=model.date,
        cash_amount=model.cash_amount,
    )


__all__ = [
    "AdjustmentStateRepository",
    "DailyBarRepository",
    "DividendRepository",
    "MissingSecurityError",
    "PersistenceRecordError",
    "PersistenceSystemError",
    "QuoteRepository",
    "SecurityRepository",
    "SqlAlchemyAdjustmentStateRepository",
    "SqlAlchemyDailyBarRepository",
    "SqlAlchemyDividendEventRepository",
    "SqlAlchemyQuoteSnapshotRepository",
    "SqlAlchemySecurityRepository",
]
