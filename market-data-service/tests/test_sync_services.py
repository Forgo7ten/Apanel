from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.domain.market_data import Adjustment, DailyBar, Dividend, Quote, Security
from app.providers.base import MarketDataProvider
from app.providers.errors import ProviderTimeoutError
from app.services.sync import (
    DailyBarSyncService,
    DividendSyncService,
    QuoteSyncService,
    SecuritySyncService,
)


def bar(symbol: str, day: int = 23) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=date(2026, 9, day),
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("100"),
        adjustment=Adjustment.NONE,
    )


class FakeProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.securities = [
            Security(symbol="600519", name="贵州茅台", market="SH", security_type="STOCK"),
            Security(symbol="000001", name="平安银行", market="SZ", security_type="STOCK"),
        ]
        self.daily: dict[str, Sequence[DailyBar]] = {
            "600519": (bar("600519"),),
            "000001": (bar("000001"),),
        }
        self.fail_daily: set[str] = set()

    async def get_symbols(self) -> Sequence[Security]:
        return self.securities

    async def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    async def get_daily_bars(self, symbol, start, end, adjustment="none") -> Sequence[DailyBar]:
        if symbol in self.fail_daily:
            raise ProviderTimeoutError("TDX daily bars timed out")
        return self.daily[symbol]

    async def get_dividends(self, symbol: str) -> Sequence[Dividend]:
        return ()


class QfqProvider(FakeProvider):
    async def get_daily_bars(self, symbol, start, end, adjustment="none") -> Sequence[DailyBar]:
        records = await super().get_daily_bars(symbol, start, end, adjustment)
        return tuple(replace(record, adjustment=adjustment) for record in records)


class MemoryRepository:
    def __init__(self, failing_symbols: set[str] | None = None) -> None:
        self.failing_symbols = failing_symbols or set()
        self.records: dict[tuple[str, date], object] = {}
        self.batches: list[tuple[object, ...]] = []

    async def upsert_many(self, records) -> None:
        batch = tuple(records)
        self.batches.append(batch)
        symbols = {record.symbol for record in batch}
        if symbols & self.failing_symbols:
            raise RuntimeError("persistence failed")
        for record in batch:
            key = (
                (record.symbol, record.trade_date)
                if hasattr(record, "trade_date")
                else (record.symbol, None)
            )
            self.records[key] = record


class SystemicRepository(MemoryRepository):
    async def upsert_many(self, records) -> None:
        raise OperationalError("INSERT", {}, OSError("database offline"))


class QuoteRepositoryMemory:
    def __init__(self) -> None:
        self.records: dict[tuple[str, datetime], Quote] = {}
        self.calls = 0

    async def upsert_many(self, records) -> None:
        self.calls += 1
        for record in records:
            self.records[(record.symbol, record.timestamp)] = record


class DividendRepositoryMemory:
    def __init__(self) -> None:
        self.records: dict[tuple[str, date], Dividend] = {}
        self.calls = 0

    async def upsert_many(self, records) -> None:
        self.calls += 1
        for record in records:
            self.records[(record.symbol, record.date)] = record


class QuoteDividendProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.quotes = {
            "600519": Quote(
                symbol="600519",
                price="10.50",
                timestamp=datetime(2026, 9, 23, 2, 30, tzinfo=UTC),
            ),
            "000001": Quote(
                symbol="000001",
                price="11.50",
                timestamp=datetime(2026, 9, 23, 2, 30, tzinfo=UTC),
            ),
        }
        self.dividends = {
            "600519": (
                Dividend(symbol="600519", date=date(2026, 6, 30), cash_amount="2"),
                Dividend(symbol="600519", date=date(2026, 6, 30), cash_amount="2.10"),
            ),
            "000001": (),
        }
        self.fail_quotes: set[str] = set()
        self.fail_dividends: set[str] = set()

    async def get_quote(self, symbol: str) -> Quote:
        if symbol in self.fail_quotes:
            raise ProviderTimeoutError("quote timed out")
        return self.quotes[symbol]

    async def get_dividends(self, symbol: str) -> Sequence[Dividend]:
        if symbol in self.fail_dividends:
            raise ProviderTimeoutError("dividends timed out")
        return self.dividends[symbol]


@pytest.mark.asyncio
async def test_security_sync_is_idempotent_and_batch_upserts() -> None:
    provider = FakeProvider()
    repository = MemoryRepository()
    service = SecuritySyncService(provider=provider, repository=repository)

    first = await service.sync()
    second = await service.sync()

    assert first.succeeded == 2
    assert first.failed == 0
    assert second.succeeded == 2
    assert len(repository.records) == 2
    assert len(repository.batches) == 2


@pytest.mark.asyncio
async def test_daily_sync_isolates_provider_and_persistence_failures_by_symbol() -> None:
    provider = FakeProvider()
    provider.fail_daily.add("000001")
    repository = MemoryRepository(failing_symbols={"600519"})
    service = DailyBarSyncService(provider=provider, repository=repository)

    result = await service.sync(
        symbols=["600519", "000001"],
        start=date(2026, 9, 1),
        end=date(2026, 9, 23),
        adjustment=Adjustment.NONE,
    )

    assert result.succeeded == 0
    assert result.failed == 2
    assert {item.symbol for item in result.items} == {"600519", "000001"}
    assert {item.error.code for item in result.items if item.error} == {
        "PERSISTENCE_ERROR",
        "PROVIDER_TIMEOUT",
    }


@pytest.mark.asyncio
async def test_daily_sync_keeps_other_symbols_when_one_persistence_batch_fails() -> None:
    provider = FakeProvider()
    repository = MemoryRepository(failing_symbols={"600519"})
    service = DailyBarSyncService(provider=provider, repository=repository)

    result = await service.sync(
        symbols=["600519", "000001"],
        start=date(2026, 9, 1),
        end=date(2026, 9, 23),
        adjustment=Adjustment.NONE,
    )

    outcomes = {item.symbol: item for item in result.items}
    assert outcomes["600519"].error is not None
    assert outcomes["600519"].error.code == "PERSISTENCE_ERROR"
    assert outcomes["000001"].succeeded
    assert "000001" in {record.symbol for record in repository.records.values()}


@pytest.mark.asyncio
async def test_daily_sync_deduplicates_bars_before_upsert() -> None:
    provider = FakeProvider()
    provider.daily["600519"] = (bar("600519"), bar("600519"))
    repository = MemoryRepository()
    service = DailyBarSyncService(provider=provider, repository=repository)

    result = await service.sync(
        symbols=["SH.600519", "600519"],
        start=date(2026, 9, 1),
        end=date(2026, 9, 23),
        adjustment=Adjustment.NONE,
    )

    assert result.succeeded == 1
    assert len(repository.records) == 1
    assert len(repository.batches[0]) == 1


@pytest.mark.asyncio
async def test_daily_sync_does_not_mask_systemic_database_failures() -> None:
    service = DailyBarSyncService(provider=FakeProvider(), repository=SystemicRepository())

    with pytest.raises(OperationalError):
        await service.sync(
            symbols=["600519", "000001"],
            start=date(2026, 9, 1),
            end=date(2026, 9, 23),
            adjustment=Adjustment.NONE,
        )


@pytest.mark.asyncio
async def test_daily_sync_defaults_to_qfq() -> None:
    repository = MemoryRepository()
    service = DailyBarSyncService(provider=QfqProvider(), repository=repository)

    result = await service.sync(
        symbols=["600519"],
        start=date(2026, 9, 1),
        end=date(2026, 9, 23),
    )

    assert result.ok
    assert next(iter(repository.records.values())).adjustment is Adjustment.QFQ


@pytest.mark.asyncio
async def test_quote_sync_persists_idempotently_and_deduplicates_symbols() -> None:
    provider = QuoteDividendProvider()
    repository = QuoteRepositoryMemory()
    service = QuoteSyncService(provider=provider, repository=repository)

    first = await service.sync(symbols=["SH.600519", "600519", "000001"])
    second = await service.sync(symbols=["600519", "000001"])

    assert first.succeeded == 2
    assert second.succeeded == 2
    assert first.items[0].persisted == 1
    assert len(repository.records) == 2
    assert repository.calls == 2


@pytest.mark.asyncio
async def test_dividend_sync_upserts_duplicate_events_and_reports_provider_failure() -> None:
    provider = QuoteDividendProvider()
    provider.fail_dividends.add("000001")
    repository = DividendRepositoryMemory()
    service = DividendSyncService(provider=provider, repository=repository)

    result = await service.sync(symbols=["600519", "000001"])

    outcomes = {item.symbol: item for item in result.items}
    assert outcomes["600519"].succeeded
    assert outcomes["600519"].fetched == 1
    assert outcomes["600519"].persisted == 1
    assert outcomes["000001"].error is not None
    assert outcomes["000001"].error.code == "PROVIDER_TIMEOUT"
    assert repository.records[("600519", date(2026, 6, 30))].cash_amount == Decimal("2.10")
