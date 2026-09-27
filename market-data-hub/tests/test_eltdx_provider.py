from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.domain.market_data import Adjustment, SecurityType
from app.providers.eltdx import EltdxProvider


class FakeCodes:
    def all(self, market: str):
        rows = {
            "sh": [
                SimpleNamespace(exchange="sh", code="600519", name="贵州茅台", category="a_share"),
                SimpleNamespace(exchange="sh", code="510300", name="沪深300ETF", category="etf"),
                SimpleNamespace(exchange="sh", code="000001", name="上证指数", category="index"),
            ],
            "sz": [
                SimpleNamespace(exchange="sz", code="000001", name="平安银行", category="a_share"),
                SimpleNamespace(exchange="sz", code="159915", name="创业板ETF", category="etf"),
            ],
            "bj": [
                SimpleNamespace(exchange="bj", code="920001", name="北交样本", category="a_share"),
            ],
        }
        return rows[market]


class FakeQuotes:
    def get_snapshots(self, codes):
        code = codes[0]
        return [
            SimpleNamespace(
                code=code[2:],
                last_price=10.5,
                change=0.5,
                change_pct=5.0,
            )
        ]


class FakeBars:
    def __init__(self) -> None:
        self.calls = []

    def get(self, code, **kwargs):
        self.calls.append((code, dict(kwargs)))
        adjust = kwargs["adjust"]
        return SimpleNamespace(
            adjust_mode=adjust,
            bars=(
                SimpleNamespace(
                    time=datetime(2026, 9, 24, 15, 0),
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume_lots=100,
                    amount=1000,
                ),
                SimpleNamespace(
                    time=datetime(2026, 9, 25, 15, 0),
                    open=10.5,
                    high=12,
                    low=10,
                    close=11.5,
                    volume_lots=120,
                    amount=1200,
                ),
            ),
        )


class FakeCorporate:
    def capital_changes(self, code):
        return SimpleNamespace(
            records=(
                SimpleNamespace(category=1, date=date(2026, 6, 30), c1_value=20.0),
                SimpleNamespace(category=1, date=date(2026, 6, 30), c1_value=5.0),
                SimpleNamespace(category=2, date=date(2026, 7, 1), c1_value=99.0),
            )
        )


class FakeClient:
    def __init__(self) -> None:
        self.codes = FakeCodes()
        self.quotes = FakeQuotes()
        self.bars = FakeBars()
        self.corporate = FakeCorporate()
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_eltdx_maps_supported_security_universe() -> None:
    provider = EltdxProvider(FakeClient())
    rows = await provider.get_symbols()
    assert [(row.symbol, row.security_type) for row in rows] == [
        ("600519", SecurityType.STOCK),
        ("510300", SecurityType.ETF),
        ("000001", SecurityType.STOCK),
        ("159915", SecurityType.ETF),
        ("920001", SecurityType.STOCK),
    ]


@pytest.mark.asyncio
async def test_eltdx_maps_quote_with_observation_timestamp() -> None:
    provider = EltdxProvider(FakeClient())
    before = datetime.now(UTC)
    quote = await provider.get_quote("600519")
    after = datetime.now(UTC)
    assert quote.symbol == "600519"
    assert str(quote.price) == "10.5"
    assert before <= quote.timestamp <= after


@pytest.mark.asyncio
async def test_eltdx_uses_server_adjustment_and_filters_date_range() -> None:
    provider = EltdxProvider(FakeClient())
    bars = await provider.get_daily_bars(
        "600519",
        date(2026, 9, 25),
        date(2026, 9, 25),
        Adjustment.QFQ,
    )
    assert len(bars) == 1
    assert bars[0].trade_date == date(2026, 9, 25)
    assert bars[0].adjustment is Adjustment.QFQ
    assert str(bars[0].volume) == "120"


@pytest.mark.asyncio
async def test_eltdx_converts_per_ten_cash_dividend_to_per_share() -> None:
    provider = EltdxProvider(FakeClient())
    rows = await provider.get_dividends("600519")
    assert len(rows) == 1
    assert rows[0].date == date(2026, 6, 30)
    assert str(rows[0].cash_amount) == "2.5"


@pytest.mark.asyncio
async def test_eltdx_batches_quote_snapshots_in_one_upstream_call() -> None:
    client = FakeClient()
    calls = []

    def snapshots(codes):
        calls.append(tuple(codes))
        return [
            SimpleNamespace(code=code[2:], last_price=10.5, change=0, change_pct=0)
            for code in codes
        ]

    client.quotes.get_snapshots = snapshots
    rows = await EltdxProvider(client).get_quotes(("600519", "000001"))
    assert [row.symbol for row in rows] == ["600519", "000001"]
    assert calls == [("sh600519", "sz000001")]


@pytest.mark.asyncio
async def test_eltdx_adjustment_revision_is_stable_for_same_actions() -> None:
    provider = EltdxProvider(FakeClient())
    assert await provider.get_adjustment_revision(
        "600519"
    ) == await provider.get_adjustment_revision("600519")


@pytest.mark.asyncio
async def test_eltdx_close_is_idempotent() -> None:
    client = FakeClient()
    provider = EltdxProvider(client)
    await provider.aclose()
    await provider.aclose()
    assert client.closed is True


@pytest.mark.asyncio
async def test_eltdx_none_and_qfq_are_forwarded_to_server_api() -> None:
    client = FakeClient()
    provider = EltdxProvider(client)
    await provider.get_daily_bars("600519", date(2026, 9, 24), date(2026, 9, 25), Adjustment.NONE)
    await provider.get_daily_bars("600519", date(2026, 9, 24), date(2026, 9, 25), Adjustment.QFQ)
    assert [call[1]["adjust"] for call in client.bars.calls] == ["none", "qfq"]
    assert all(call[1]["count"] > 0 for call in client.bars.calls)
    assert all("all_pages" not in call[1] for call in client.bars.calls)


@pytest.mark.asyncio
async def test_eltdx_rejects_quote_symbol_mismatch() -> None:
    client = FakeClient()
    client.quotes.get_snapshots = lambda codes: [
        SimpleNamespace(code="000001", last_price=10, change=0, change_pct=0)
    ]
    provider = EltdxProvider(client)
    with pytest.raises(Exception, match="symbol does not match"):
        await provider.get_quote("600519")


@pytest.mark.asyncio
async def test_eltdx_rejects_adjustment_mismatch() -> None:
    client = FakeClient()

    def wrong_adjustment(code, **kwargs):
        return SimpleNamespace(adjust_mode="none", bars=())

    client.bars.get = wrong_adjustment
    provider = EltdxProvider(client)
    with pytest.raises(Exception, match="adjustment does not match"):
        await provider.get_daily_bars(
            "600519", date(2026, 9, 24), date(2026, 9, 25), Adjustment.QFQ
        )


@pytest.mark.asyncio
async def test_eltdx_timeout_is_mapped_to_provider_timeout() -> None:
    from eltdx.exceptions import ResponseTimeoutError

    from app.providers.errors import ProviderTimeoutError

    client = FakeClient()

    def timeout(codes):
        raise ResponseTimeoutError("timeout")

    client.quotes.get_snapshots = timeout
    provider = EltdxProvider(client)
    with pytest.raises(ProviderTimeoutError):
        await provider.get_quote("600519")


@pytest.mark.asyncio
async def test_eltdx_pool_busy_is_mapped_to_provider_unavailable() -> None:
    from eltdx.exceptions import PoolBusyError

    from app.providers.errors import ProviderUnavailableError

    client = FakeClient()

    def busy(codes):
        raise PoolBusyError("busy")

    client.quotes.get_snapshots = busy
    provider = EltdxProvider(client)
    with pytest.raises(ProviderUnavailableError):
        await provider.get_quote("600519")


@pytest.mark.asyncio
async def test_eltdx_ignores_zero_cash_and_non_dividend_actions() -> None:
    client = FakeClient()
    client.corporate.capital_changes = lambda code: SimpleNamespace(
        records=(
            SimpleNamespace(category=1, date=date(2026, 1, 1), c1_value=0),
            SimpleNamespace(category=2, date=date(2026, 2, 1), c1_value=100),
            SimpleNamespace(category=1, date=date(2026, 3, 1), c1_value=10),
        )
    )
    provider = EltdxProvider(client)
    rows = await provider.get_dividends("600519")
    assert [(row.date, str(row.cash_amount)) for row in rows] == [(date(2026, 3, 1), "1")]
