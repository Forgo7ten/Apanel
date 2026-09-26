import asyncio
import threading
from collections.abc import Sequence

import pytest

from app.domain.market_data import SecurityType
from app.providers.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.security import AkshareSecurityProvider


class FakeFrame:
    def __init__(self, records: Sequence[dict[str, object]]) -> None:
        self.records = tuple(records)
        self.orients: list[str] = []

    def to_dict(self, orient: str = "dict") -> list[dict[str, object]]:
        self.orients.append(orient)
        return list(self.records)


def stock_record(code: str = "600519", name: str = "贵州茅台") -> dict[str, object]:
    return {"code": code, "name": name}


def etf_record(code: str = "510300", name: str = "沪深300ETF") -> dict[str, object]:
    return {"代码": code, "名称": name}


@pytest.mark.asyncio
async def test_akshare_maps_dataframe_stock_and_etf_records_to_one_batch() -> None:
    stock = FakeFrame(
        [
            stock_record("600519", "贵州茅台"),
            stock_record("000001", "平安银行"),
            stock_record("920001", "北证样本"),
        ]
    )
    etf = FakeFrame([etf_record()])
    provider = AkshareSecurityProvider(
        stock_fetcher=lambda: stock,
        etf_fetcher=lambda: etf,
        min_stock_count=3,
        min_etf_count=1,
    )

    records = await provider.get_symbols()

    assert [(record.symbol, record.market.value, record.security_type) for record in records] == [
        ("600519", "SH", SecurityType.STOCK),
        ("000001", "SZ", SecurityType.STOCK),
        ("920001", "BJ", SecurityType.STOCK),
        ("510300", "SH", SecurityType.ETF),
    ]
    assert all(record.name for record in records)
    assert stock.orients == ["records"]
    assert etf.orients == ["records"]


@pytest.mark.asyncio
async def test_akshare_deduplicates_identical_rows_but_keeps_source_atomic() -> None:
    provider = AkshareSecurityProvider(
        stock_fetcher=lambda: [stock_record(), stock_record()],
        etf_fetcher=lambda: [etf_record(), etf_record()],
        min_stock_count=1,
        min_etf_count=1,
    )

    records = await provider.get_symbols()

    assert [(record.symbol, record.security_type) for record in records] == [
        ("600519", SecurityType.STOCK),
        ("510300", SecurityType.ETF),
    ]


@pytest.mark.asyncio
async def test_akshare_rejects_same_symbol_with_conflicting_source_metadata() -> None:
    provider = AkshareSecurityProvider(
        stock_fetcher=lambda: [stock_record("510300", "股票视图")],
        etf_fetcher=lambda: [etf_record("510300", "沪深300ETF")],
        min_stock_count=1,
        min_etf_count=1,
    )

    with pytest.raises(ProviderUnavailableError):
        await provider.get_symbols()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stock_records", "etf_records", "message"),
    [
        ([], [etf_record()], "empty"),
        ([{"code": "600519"}], [etf_record()], "column"),
        ([stock_record(name="")], [etf_record()], "name"),
        ([stock_record("not-a-code")], [etf_record()], "symbol"),
        ([stock_record(), stock_record(name="冲突")], [etf_record()], "duplicate"),
        ([stock_record()], [], "empty"),
        ([stock_record()], [{"代码": "510300"}], "column"),
    ],
)
async def test_akshare_rejects_invalid_or_incomplete_source_as_a_whole(
    stock_records: list[dict[str, object]],
    etf_records: list[dict[str, object]],
    message: str,
) -> None:
    provider = AkshareSecurityProvider(
        stock_fetcher=lambda: stock_records,
        etf_fetcher=lambda: etf_records,
        min_stock_count=1,
        min_etf_count=1,
    )

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await provider.get_symbols()

    assert message  # Keep the parametrization names readable in failure output.
    assert "http" not in str(exc_info.value).casefold()
    assert "akshare" in str(exc_info.value).casefold()


@pytest.mark.asyncio
async def test_akshare_enforces_conservative_configurable_minimum_counts() -> None:
    provider = AkshareSecurityProvider(
        stock_fetcher=lambda: [stock_record()],
        etf_fetcher=lambda: [etf_record()],
        min_stock_count=2,
        min_etf_count=2,
    )

    with pytest.raises(ProviderUnavailableError):
        await provider.get_symbols()


@pytest.mark.asyncio
async def test_akshare_timeout_class_hides_upstream_timeout_details() -> None:
    def fetch_stock() -> list[dict[str, object]]:
        raise ProviderTimeoutError("upstream timeout https://secret.invalid")

    provider = AkshareSecurityProvider(
        stock_fetcher=fetch_stock,
        etf_fetcher=lambda: [etf_record()],
        timeout_seconds=0.5,
        min_stock_count=1,
        min_etf_count=1,
    )

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await provider.get_symbols()

    assert str(exc_info.value) == "AKShare security master timed out after 0.5s"
    assert "secret.invalid" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_akshare_clears_function_cache_before_each_source_call() -> None:
    calls: list[str] = []

    def fetch_stock() -> list[dict[str, object]]:
        calls.append("stock")
        return [stock_record()]

    def clear_stock() -> None:
        calls.append("clear-stock")

    def fetch_etf() -> list[dict[str, object]]:
        calls.append("etf")
        return [etf_record()]

    def clear_etf() -> None:
        calls.append("clear-etf")

    fetch_stock.cache_clear = clear_stock  # type: ignore[attr-defined]
    fetch_etf.cache_clear = clear_etf  # type: ignore[attr-defined]
    provider = AkshareSecurityProvider(
        stock_fetcher=fetch_stock,
        etf_fetcher=fetch_etf,
        min_stock_count=1,
        min_etf_count=1,
    )

    await provider.get_symbols()

    assert calls == ["clear-stock", "stock", "clear-etf", "etf"]


@pytest.mark.asyncio
async def test_akshare_timeout_keeps_one_blocking_flight_until_thread_finishes() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = 0

    def fetch_stock() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        finished.set()
        return [stock_record()]

    provider = AkshareSecurityProvider(
        stock_fetcher=fetch_stock,
        etf_fetcher=lambda: [etf_record()],
        timeout_seconds=0.02,
        min_stock_count=1,
        min_etf_count=1,
    )

    with pytest.raises(ProviderTimeoutError):
        await provider.get_symbols()
    assert started.wait(timeout=1)

    second_call = asyncio.create_task(provider.get_symbols())
    await asyncio.sleep(0.04)
    assert calls == 1
    release.set()

    with pytest.raises(ProviderTimeoutError):
        await second_call
    assert calls == 1
    assert finished.wait(timeout=1)
    await asyncio.sleep(0.01)

    records = await provider.get_symbols()
    assert [record.symbol for record in records] == ["600519", "510300"]
    assert calls == 2


@pytest.mark.asyncio
async def test_akshare_waiters_share_one_absolute_deadline() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fetch_stock() -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        return [stock_record()]

    provider = AkshareSecurityProvider(
        stock_fetcher=fetch_stock,
        etf_fetcher=lambda: [etf_record()],
        timeout_seconds=0.08,
        min_stock_count=1,
        min_etf_count=1,
    )
    first = asyncio.create_task(provider.get_symbols())
    assert await asyncio.to_thread(started.wait, 1)
    await asyncio.sleep(0.03)
    second = asyncio.create_task(provider.get_symbols())
    await asyncio.sleep(0)

    try:
        with pytest.raises(ProviderTimeoutError):
            await first
        # The second waiter joined the original flight.  Its remaining wait
        # is already exhausted; it must not get another full 80 ms.
        with pytest.raises(ProviderTimeoutError):
            await asyncio.wait_for(second, timeout=0.02)
        assert calls == 1
    finally:
        release.set()
        await provider.aclose()


@pytest.mark.asyncio
async def test_akshare_aclose_waits_for_worker_and_is_idempotent() -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def fetch_stock() -> list[dict[str, object]]:
        started.set()
        release.wait(timeout=2)
        finished.set()
        return [stock_record()]

    provider = AkshareSecurityProvider(
        stock_fetcher=fetch_stock,
        etf_fetcher=lambda: [etf_record()],
        timeout_seconds=1,
        shutdown_timeout_seconds=0.2,
        min_stock_count=1,
        min_etf_count=1,
    )
    flight = asyncio.create_task(provider.get_symbols())
    assert await asyncio.to_thread(started.wait, 1)
    closing = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0.02)
    assert not closing.done()

    release.set()
    await closing
    assert finished.is_set()
    await flight
    await provider.aclose()
    with pytest.raises(ProviderUnavailableError):
        await provider.get_symbols()


@pytest.mark.asyncio
async def test_akshare_aclose_has_a_finite_shutdown_timeout() -> None:
    started = threading.Event()
    release = threading.Event()

    def fetch_stock() -> list[dict[str, object]]:
        started.set()
        release.wait(timeout=2)
        return [stock_record()]

    provider = AkshareSecurityProvider(
        stock_fetcher=fetch_stock,
        etf_fetcher=lambda: [etf_record()],
        timeout_seconds=1,
        shutdown_timeout_seconds=0.02,
        min_stock_count=1,
        min_etf_count=1,
    )
    flight = asyncio.create_task(provider.get_symbols())
    assert await asyncio.to_thread(started.wait, 1)

    started_closing = asyncio.get_running_loop().time()
    await provider.aclose()
    elapsed = asyncio.get_running_loop().time() - started_closing

    assert elapsed >= 0.01
    assert elapsed < 0.2
    release.set()
    await flight
