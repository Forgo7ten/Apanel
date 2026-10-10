"""Reject ambiguous facts instead of choosing an order-dependent provider winner."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.domain.market_data import Adjustment, Dividend, InvalidMarketDataError, Quote
from app.providers.eltdx import EltdxProvider
from app.repositories.market_data import PersistenceRecordError, _unique_records
from app.services.sync import (
    DailyBarSyncService,
    QuoteSyncService,
    _validated_bars,
    _validated_dividends,
)
from tests.test_sync_services import FakeProvider, MemoryRepository, QuoteRepositoryMemory, bar


@pytest.mark.parametrize("reverse", [False, True])
async def test_conflicting_bars_fail_only_affected_security(reverse):
    provider = FakeProvider()
    original = bar("600519")
    pair = [original, replace(original, close=Decimal("10.6"))]
    provider.daily["600519"] = pair[::-1] if reverse else pair
    repository = MemoryRepository()
    result = await DailyBarSyncService(provider=provider, repository=repository).sync(
        symbols=["600519", "000001"],
        start=date(2026, 9, 1),
        end=date(2026, 9, 23),
        adjustment=Adjustment.NONE,
    )
    outcomes = {item.symbol: item for item in result.items}
    assert not outcomes["600519"].succeeded
    assert outcomes["000001"].succeeded
    assert all(key[0] != "600519" for key in repository.records)


def test_equal_duplicates_are_idempotent_and_dates_are_sorted():
    older, newer = bar("600519", 2), bar("600519", 3)
    assert _validated_bars("600519", [newer, older, newer], Adjustment.NONE) == (older, newer)


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_dividend_totals_are_not_overwritten(reverse):
    first = Dividend(symbol="600519", date=date(2026, 9, 1), cash_amount=Decimal("1"))
    second = replace(first, cash_amount=Decimal("2"))
    values = [first, second]
    with pytest.raises(InvalidMarketDataError, match="conflict"):
        _validated_dividends("600519", values[::-1] if reverse else values)


def test_repository_also_rejects_conflicting_records():
    first = bar("600519")
    with pytest.raises(PersistenceRecordError, match="conflict"):
        _unique_records(
            [first, replace(first, close=Decimal("10.6"))],
            key=lambda item: (item.symbol, item.trade_date, item.adjustment),
        )


@pytest.mark.parametrize("conflict", [False, True])
async def test_overlapping_provider_pages_cannot_silently_change_a_bar(conflict):
    def raw(day, close=10.5):
        return SimpleNamespace(
            time=datetime(2026, 9, day, 15),
            open=10,
            high=11,
            low=9,
            close=close,
            volume_lots=100,
            amount=1000,
        )

    class Bars:
        def get(self, codes, **kwargs):
            rows = (
                [raw(3), raw(2)]
                if kwargs["start"] == 0
                else [raw(2, 10.6 if conflict else 10.5), raw(1)]
            )
            return {"sh600519": SimpleNamespace(adjust_mode="none", bars=rows)}

    provider = EltdxProvider(SimpleNamespace(bars=Bars()), bar_page_size=2)
    if conflict:
        with pytest.raises(InvalidMarketDataError, match="conflict"):
            await provider.get_daily_bars_batch(["600519"], adjustment="none", count=3)
    else:
        rows = await provider.get_daily_bars_batch(["600519"], adjustment="none", count=3)
        assert [item.trade_date.day for item in rows["600519"]] == [1, 2, 3]


@pytest.mark.parametrize("reverse", [False, True])
async def test_quote_conflict_isolated_and_newest_source_timestamp_wins(reverse):
    first = Quote(symbol="600519", price="10", timestamp=datetime(2026, 9, 1, tzinfo=UTC))
    conflict = replace(first, price=Decimal("11"))
    old = replace(first, symbol="000001")
    new = replace(old, price=Decimal("12"), timestamp=old.timestamp + timedelta(minutes=1))

    class Provider:
        async def get_quotes(self, symbols):
            values = [first, conflict, new, old]
            return values[::-1] if reverse else values

    repository = QuoteRepositoryMemory()
    summary = await QuoteSyncService(provider=Provider(), repository=repository).sync(
        symbols=["600519", "000001"]
    )
    outcomes = {item.symbol: item for item in summary.items}
    assert not outcomes["600519"].succeeded
    assert outcomes["000001"].succeeded
    assert list(repository.records.values()) == [new]


async def test_lookback_validates_conflicts_before_truncating():
    first = bar("600519")

    class Provider:
        async def get_daily_bars_batch(self, symbols, **kwargs):
            return {"600519": [first, replace(first, close=Decimal("10.6"))]}

    repository = MemoryRepository()
    summary = await DailyBarSyncService(provider=Provider(), repository=repository).sync(
        symbols=["600519"], lookback_bars=1, end=first.trade_date, adjustment=Adjustment.NONE
    )
    assert not summary.ok
    assert not repository.records


@pytest.mark.asyncio
async def test_eltdx_conflicting_quote_keeps_other_symbol_and_does_not_write_bad():
    calls = []

    class Quotes:
        def get_snapshots(self, codes):
            calls.append(tuple(codes))
            result = []
            for code in codes:
                price = 12 if code.startswith("sz") else 10
                result.append(
                    SimpleNamespace(
                        code=code[2:],
                        last_price=price,
                        change=0,
                        change_pct=0,
                    )
                )
                if code.startswith("sh"):
                    result.append(
                        SimpleNamespace(
                            code=code[2:],
                            last_price=11,
                            change=1,
                            change_pct=10,
                        )
                    )
            return result

    provider = EltdxProvider(SimpleNamespace(quotes=Quotes()))
    repo = QuoteRepositoryMemory()
    result = await QuoteSyncService(provider=provider, repository=repo).sync(
        symbols=["600519", "000001"],
    )
    outcomes = {item.symbol: item for item in result.items}
    assert outcomes["600519"].error.code == "INVALID_MARKET_DATA"
    assert outcomes["000001"].succeeded
    assert {(quote.symbol, quote.price) for quote in repo.records.values()} == {
        ("000001", Decimal("12"))
    }
    assert calls == [("sh600519", "sz000001"), ("sh600519",), ("sz000001",)]


@pytest.mark.asyncio
async def test_eltdx_conflicting_daily_bar_keeps_good_symbol():
    calls = []

    def raw(close):
        return SimpleNamespace(
            time=datetime(2026, 9, 25, 15),
            open=10,
            high=20,
            low=9,
            close=close,
            volume_lots=100,
            amount=1000,
        )

    class Bars:
        def get(self, codes, **kwargs):
            calls.append(tuple(codes))
            result = {}
            for code in codes:
                records = [raw(15)]
                if code.startswith("sh"):
                    records.append(raw(16))
                result[code] = SimpleNamespace(adjust_mode="none", bars=records)
            return result

    repo = MemoryRepository()
    provider = EltdxProvider(SimpleNamespace(bars=Bars()))
    result = await DailyBarSyncService(provider=provider, repository=repo).sync(
        symbols=["600519", "000001"],
        adjustment="none",
        start=date(2026, 9, 25),
        end=date(2026, 9, 25),
    )
    outcomes = {item.symbol: item for item in result.items}
    assert outcomes["600519"].error.code == "INVALID_MARKET_DATA"
    assert outcomes["000001"].succeeded
    assert {(key[0], value.close) for key, value in repo.records.items()} == {
        ("000001", Decimal("15"))
    }
    assert calls == [("sh600519", "sz000001"), ("sh600519",), ("sz000001",)]


@pytest.mark.asyncio
async def test_infrastructure_quote_failure_never_fans_out_to_single_symbols():
    calls = []

    class Provider:
        async def get_quotes(self, symbols):
            calls.append(tuple(symbols))
            raise ProviderUnavailableError("source unavailable")

    from app.providers.errors import ProviderUnavailableError

    repo = QuoteRepositoryMemory()
    result = await QuoteSyncService(provider=Provider(), repository=repo).sync(
        symbols=["600519", "000001"],
    )
    assert result.failed == 2
    assert calls == [("600519", "000001")]
    assert not repo.records


@pytest.mark.asyncio
async def test_infrastructure_daily_failure_never_fans_out_to_single_symbols():
    from app.providers.errors import ProviderUnavailableError

    calls = []

    class Provider:
        async def get_daily_bars_batch(self, symbols, **kwargs):
            calls.append(tuple(symbols))
            raise ProviderUnavailableError("source unavailable")

    repo = MemoryRepository()
    result = await DailyBarSyncService(provider=Provider(), repository=repo).sync(
        symbols=["600519", "000001"],
        start=date(2026, 9, 25),
        end=date(2026, 9, 25),
        adjustment="none",
    )
    assert result.failed == 2
    assert calls == [("600519", "000001")]
    assert not repo.records
