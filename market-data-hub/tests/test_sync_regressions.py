"""Expected-correct regressions for unmodified Hub; no upstream network calls."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.market_data import Adjustment, Quote
from app.services.sync import DailyBarSyncService, QuoteSyncService
from tests.test_sync_services import MemoryRepository, QuoteRepositoryMemory, bar

pytestmark = pytest.mark.asyncio


async def test_missing_symbol_is_not_empty_success():
    class Provider:
        async def get_daily_bars_batch(self, symbols, **kwargs):
            return {"600519": (bar("600519"),)}

    service = DailyBarSyncService(provider=Provider(), repository=MemoryRepository())
    result = await service.sync(
        symbols=["600519", "000001"],
        lookback_bars=2,
        end=date(2026, 9, 23),
        adjustment=Adjustment.NONE,
    )
    missing = next(item for item in result.items if item.symbol == "000001")
    assert not missing.succeeded, "absent symbol was converted into success(fetched=0)"


async def test_quote_batch_missing_first_symbol_preserves_later_valid_symbols():
    class Provider:
        async def get_quotes(self, symbols):
            return (Quote(symbol="600519", price=Decimal("10"), timestamp=datetime.now(UTC)),)

    repository = QuoteRepositoryMemory()
    service = QuoteSyncService(provider=Provider(), repository=repository)
    result = await service.sync(symbols=["000001", "600519"])
    healthy = next(item for item in result.items if item.symbol == "600519")
    assert healthy.succeeded, "missing earlier symbol aborted processing of later valid symbol"


async def test_rebase_capped_at_old_date_preserves_later_bars():
    class Provider:
        async def get_adjustment_revision(self, symbol):
            return "r2"

        async def get_daily_bars_batch(self, symbols, **kwargs):
            return {
                "600519": tuple(
                    replace(bar("600519", i), adjustment=Adjustment.QFQ) for i in [2, 3, 4]
                )
            }

    class Revision:
        async def get(self, symbol):
            return {"revision_hash": "r1"}

        async def upsert(self, *args, **kwargs):
            pass

    repository = MemoryRepository()
    for i in [2, 3, 4]:
        repository.records[("600519", date(2026, 9, i))] = replace(
            bar("600519", i), adjustment=Adjustment.QFQ
        )
    service = DailyBarSyncService(
        provider=Provider(), repository=repository, adjustment_repository=Revision()
    )
    await service.sync(
        symbols=["600519"], lookback_bars=3, end=date(2026, 9, 3), adjustment=Adjustment.QFQ
    )
    assert ("600519", date(2026, 9, 4)) in repository.records, (
        "replay end cutoff deleted later stored bars"
    )


async def test_unverified_revision_cannot_delete_old_dates():
    class Provider:
        async def get_adjustment_revision(self, symbol):
            return "r2"

        async def get_daily_bars_batch(self, symbols, **kwargs):
            return {
                "600519": tuple(
                    replace(bar("600519", i), adjustment=Adjustment.QFQ) for i in [2, 4]
                )
            }

    class Revision:
        async def get(self, symbol):
            return {"revision_hash": "r1"}

        async def upsert(self, *args, **kwargs):
            raise AssertionError("untrusted revision must not be accepted")

    repository = MemoryRepository()
    for i in [2, 3, 4]:
        repository.records[("600519", date(2026, 9, i))] = replace(
            bar("600519", i), adjustment=Adjustment.QFQ
        )
    result = await DailyBarSyncService(
        provider=Provider(), repository=repository, adjustment_repository=Revision()
    ).sync(symbols=["600519"], lookback_bars=3, end=date(2026, 9, 4))
    assert not result.ok
    assert len(repository.records) == 3


async def test_revision_hash_changing_during_fetch_is_not_published():
    class Provider:
        calls = 0

        async def get_adjustment_revision(self, symbol):
            self.calls += 1
            return "r2" if self.calls == 1 else "r3"

        async def get_daily_bars_batch(self, symbols, **kwargs):
            return {
                "600519": tuple(
                    replace(bar("600519", i), adjustment=Adjustment.QFQ) for i in [2, 3, 4]
                )
            }

    class Revision:
        async def get(self, symbol):
            return {"revision_hash": "r1"}

        async def upsert(self, *args, **kwargs):
            raise AssertionError("revision is unstable")

    repository = MemoryRepository()
    repository.records[("600519", date(2026, 9, 2))] = replace(
        bar("600519", 2), adjustment=Adjustment.QFQ
    )
    result = await DailyBarSyncService(
        provider=Provider(), repository=repository, adjustment_repository=Revision()
    ).sync(symbols=["600519"], lookback_bars=3, end=date(2026, 9, 4))
    assert not result.ok
    assert len(repository.records) == 1


async def test_empty_present_series_is_distinct_from_a_missing_symbol():
    class Provider:
        async def get_daily_bars_batch(self, symbols, **kwargs):
            return {"600519": ()}

    result = await DailyBarSyncService(provider=Provider(), repository=MemoryRepository()).sync(
        symbols=["600519", "000001"], lookback_bars=3, adjustment=Adjustment.NONE
    )
    statuses = {item.symbol: item.succeeded for item in result.items}
    assert statuses == {"600519": True, "000001": False}


async def test_primary_master_missing_exchange_is_unavailable():
    from app.providers.eltdx import EltdxProvider
    from app.providers.errors import ProviderUnavailableError
    from tests.test_eltdx_provider import FakeClient

    client = FakeClient()
    original = client.codes.all
    client.codes.all = lambda market: [] if market == "bj" else original(market)
    with pytest.raises(ProviderUnavailableError, match="exchange"):
        await EltdxProvider(client).get_symbols()


async def test_history_pages_respect_800_rows_and_count_after_cutoff():
    from datetime import timedelta
    from types import SimpleNamespace

    from app.providers.eltdx import EltdxProvider
    from tests.test_eltdx_provider import FakeClient

    client = FakeClient()
    start = datetime(2020, 1, 1)
    rows = [
        SimpleNamespace(
            time=start + timedelta(days=i),
            open=10,
            high=11,
            low=9,
            close=10,
            volume_lots=100,
            amount=1000,
        )
        for i in range(1200)
    ]
    calls = []

    def get(codes, **kwargs):
        calls.append(kwargs)
        assert 0 < kwargs["count"] <= 800
        stop = len(rows) - kwargs["start"]
        items = rows[max(0, stop - kwargs["count"]) : max(0, stop)]
        return {
            code: SimpleNamespace(adjust_mode=kwargs["adjust"], bars=tuple(items)) for code in codes
        }

    client.bars.get = get
    cutoff = (start + timedelta(days=999)).date()
    result = await EltdxProvider(client).get_daily_bars_window(["600519"], count=900, end=cutoff)
    assert len(result["600519"]) == 900
    assert result["600519"][-1].trade_date == cutoff
    assert result["600519"][0].trade_date == (start + timedelta(days=100)).date()
    assert [call["start"] for call in calls] == [0, 800]


async def test_incomplete_old_history_cannot_succeed_as_empty_after_paging_limit():
    from types import SimpleNamespace

    from app.providers.eltdx import EltdxProvider
    from app.providers.errors import ProviderUnavailableError
    from tests.test_eltdx_provider import FakeClient

    client = FakeClient()
    row = SimpleNamespace(
        time=datetime(2026, 9, 24), open=10, high=11, low=9, close=10, volume_lots=100
    )
    client.bars.get = lambda codes, **kwargs: {
        code: SimpleNamespace(adjust_mode="qfq", bars=(row,) * kwargs["count"]) for code in codes
    }
    with pytest.raises(ProviderUnavailableError, match="paging budget"):
        await EltdxProvider(client, bar_max_pages=2).get_daily_bars_window(
            ["600519"], count=2, end=date(2020, 1, 1)
        )


async def test_unverified_universe_keeps_missing_security_and_rejects_large_shrinkage():
    from sqlalchemy import create_engine, select

    from app.domain.market_data import Security
    from app.models.market_data import Base, SecurityModel
    from app.repositories.market_data import PersistenceRecordError, SqlAlchemySecurityRepository
    from tests.test_persistence_contract import SyncSessionFactory

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repository = SqlAlchemySecurityRepository(SyncSessionFactory(engine))
    records = [
        Security(symbol=str(600000 + i), name=f"Test-{i}", market="SH", security_type="STOCK")
        for i in range(40)
    ]
    await repository.reconcile_snapshot(records)
    await repository.reconcile_snapshot(records[1:])
    with engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    select(SecurityModel.id).where(SecurityModel.status == "ACTIVE")
                ).all()
            )
            == 40
        )
    with pytest.raises(PersistenceRecordError, match="safety threshold"):
        await repository.reconcile_snapshot(records[:8], authoritative=True)
    await repository.reconcile_snapshot(records[1:], authoritative=True)
    with engine.connect() as connection:
        assert (
            len(
                connection.execute(
                    select(SecurityModel.id).where(SecurityModel.status == "ACTIVE")
                ).all()
            )
            == 39
        )
    engine.dispose()
