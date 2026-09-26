"""Security-master provider seams and the AKShare fallback adapter.

The security universe is deliberately a narrower seam than the full
``MarketDataProvider`` contract.  TDX remains the owner of quotes, daily
bars, and dividends; this module only composes the security-list operation.
AKShare is imported inside the blocking worker, so constructing the fallback
never changes the startup path or the TDX-only path.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.domain.market_data import InvalidMarketDataError, Security, SecurityType, infer_market
from app.providers.errors import ProviderTimeoutError, ProviderUnavailableError

from .base import SecurityMasterProvider

SecurityFetcher = Callable[[], object]
SymbolProvider = SecurityMasterProvider

_AKSHARE_UNAVAILABLE = "AKShare security master is unavailable."
_SECURITY_MASTER_UNAVAILABLE = "Security master providers are unavailable."


class AkshareSecurityProvider:
    """Fetch and validate the complete stock/ETF universe from AKShare.

    AKShare's APIs are synchronous and can retain process-wide cached frames.
    Both source calls therefore run in one worker thread, under one total
    timeout and one in-flight task.  Shielding that task on timeout is
    intentional: cancelling the coroutine must not allow a second blocking
    AKShare request to overlap the first one in a long-running service.
    """

    name = "akshare-security"

    def __init__(
        self,
        *,
        stock_fetcher: SecurityFetcher | None = None,
        etf_fetcher: SecurityFetcher | None = None,
        timeout_seconds: float = 90.0,
        min_stock_count: int = 1000,
        min_etf_count: int = 1,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if min_stock_count <= 0:
            raise ValueError("min_stock_count must be greater than zero")
        if min_etf_count <= 0:
            raise ValueError("min_etf_count must be greater than zero")
        self._stock_fetcher = stock_fetcher or _default_stock_fetcher
        self._etf_fetcher = etf_fetcher or _default_etf_fetcher
        self._timeout_seconds = timeout_seconds
        self._min_stock_count = min_stock_count
        self._min_etf_count = min_etf_count
        self._inflight: asyncio.Task[tuple[Security, ...]] | None = None
        self._closed = False

    async def get_symbols(self) -> Sequence[Security]:
        """Return a complete source batch or a safe provider error."""

        if self._closed:
            raise ProviderUnavailableError(_AKSHARE_UNAVAILABLE)

        # There is no await between reading and assigning ``_inflight``.  A
        # second coroutine on this event loop therefore observes the same task
        # and joins it instead of starting another blocking source call.
        flight = self._inflight
        if flight is None or flight.done():
            flight = asyncio.create_task(self._fetch_batch())
            self._inflight = flight
            flight.add_done_callback(self._clear_inflight)

        try:
            return await asyncio.wait_for(
                asyncio.shield(flight),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"AKShare security master timed out after {self._timeout_seconds:g}s"
            ) from exc
        except ProviderTimeoutError:
            raise
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            # Do not expose pandas, HTTP, URL, or upstream response details to
            # API callers.  The original exception remains available only as a
            # controlled traceback cause for internal logs.
            raise ProviderUnavailableError(_AKSHARE_UNAVAILABLE) from exc

    async def aclose(self) -> None:
        """Mark this stateless adapter closed; no client thread is retained."""

        self._closed = True

    async def _fetch_batch(self) -> tuple[Security, ...]:
        try:
            return await asyncio.to_thread(self._fetch_batch_blocking)
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(_AKSHARE_UNAVAILABLE) from exc

    def _fetch_batch_blocking(self) -> tuple[Security, ...]:
        stock_rows = _records_from_source(
            _call_source(self._stock_fetcher),
            source="stock",
            columns=("code", "name"),
        )
        stocks = _validate_source(
            stock_rows,
            source="stock",
            code_key="code",
            name_key="name",
            security_type=SecurityType.STOCK,
            minimum=self._min_stock_count,
        )

        etf_rows = _records_from_source(
            _call_source(self._etf_fetcher),
            source="ETF",
            columns=("代码", "名称"),
        )
        etfs = _validate_source(
            etf_rows,
            source="ETF",
            code_key="代码",
            name_key="名称",
            security_type=SecurityType.ETF,
            minimum=self._min_etf_count,
        )

        combined = (*stocks, *etfs)
        seen: dict[str, Security] = {}
        for record in combined:
            previous = seen.get(record.symbol)
            if previous is not None:
                raise InvalidMarketDataError(
                    f"AKShare security sources contain a duplicate symbol: {record.symbol}"
                )
            seen[record.symbol] = record
        return combined

    def _clear_inflight(self, task: asyncio.Task[tuple[Security, ...]]) -> None:
        if self._inflight is task:
            self._inflight = None


class SecurityMasterFallbackProvider:
    """Use TDX security metadata first, then one complete AKShare batch."""

    name = "security-master-fallback"

    def __init__(
        self,
        *,
        primary: SecurityMasterProvider,
        fallback: SecurityMasterProvider | None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._closed = False

    async def get_symbols(self) -> Sequence[Security]:
        """Return one source's complete batch; never concatenate sources."""

        try:
            primary_records = tuple(await self._primary.get_symbols())
            if primary_records:
                return primary_records
        except Exception:
            # Primary diagnostics are intentionally not propagated.  The
            # fallback decision must not leak provider response details.
            pass

        fallback = self._fallback
        if fallback is None:
            raise ProviderUnavailableError(_SECURITY_MASTER_UNAVAILABLE)
        try:
            fallback_records = tuple(await fallback.get_symbols())
        except Exception as exc:
            raise ProviderUnavailableError(_SECURITY_MASTER_UNAVAILABLE) from exc
        if not fallback_records:
            raise ProviderUnavailableError(_SECURITY_MASTER_UNAVAILABLE)
        return fallback_records

    async def aclose(self) -> None:
        """Close only the fallback owned by this composite, exactly once.

        The primary TDX provider is also used by quote/daily/dividend
        services, so its lifecycle remains owned by the application and is
        closed separately there.  This avoids double-closing a TDX client.
        """

        if self._closed:
            return
        self._closed = True
        fallback = self._fallback
        if fallback is None:
            return
        close = getattr(fallback, "aclose", None)
        if close is None:
            close = getattr(fallback, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result


def _default_stock_fetcher() -> object:
    ak = importlib.import_module("akshare")
    fetcher = ak.stock_info_a_code_name
    _clear_cache(fetcher)
    return fetcher()


def _default_etf_fetcher() -> object:
    ak = importlib.import_module("akshare")
    fetcher = ak.fund_etf_spot_em
    _clear_cache(fetcher)
    return fetcher()


def _call_source(fetcher: SecurityFetcher) -> object:
    _clear_cache(fetcher)
    return fetcher()


def _clear_cache(fetcher: object) -> None:
    clear_cache = getattr(fetcher, "cache_clear", None)
    if callable(clear_cache):
        clear_cache()


def _records_from_source(
    value: object,
    *,
    source: str,
    columns: tuple[str, str],
) -> tuple[Mapping[str, Any], ...]:
    """Convert a pandas DataFrame or test-friendly record sequence."""

    if value is None:
        raise InvalidMarketDataError(f"AKShare {source} source is empty")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        value = to_dict("records")
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise InvalidMarketDataError(f"AKShare {source} source must be a record sequence")
    records: list[Mapping[str, Any]] = []
    if not value:
        raise InvalidMarketDataError(f"AKShare {source} source is empty")
    for row in value:
        if not isinstance(row, Mapping):
            raise InvalidMarketDataError(f"AKShare {source} record must be a mapping")
        if any(column not in row for column in columns):
            raise InvalidMarketDataError(
                f"AKShare {source} source is missing required columns"
            )
        records.append(row)
    return tuple(records)


def _validate_source(
    records: Sequence[Mapping[str, Any]],
    *,
    source: str,
    code_key: str,
    name_key: str,
    security_type: SecurityType,
    minimum: int,
) -> tuple[Security, ...]:
    if len(records) < minimum:
        raise InvalidMarketDataError(
            f"AKShare {source} source contains too few records"
        )
    result: list[Security] = []
    seen: set[str] = set()
    for row in records:
        raw_code = row.get(code_key)
        raw_name = row.get(name_key)
        if isinstance(raw_code, bool) or raw_code is None:
            raise InvalidMarketDataError(f"AKShare {source} code is invalid")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise InvalidMarketDataError(f"AKShare {source} name is empty")
        code = str(raw_code).strip()
        try:
            market = infer_market(code)
            record = Security(
                symbol=code,
                name=raw_name,
                market=market,
                security_type=security_type,
                exchange=market.value,
            )
        except Exception as exc:
            raise InvalidMarketDataError(f"AKShare {source} code is invalid") from exc
        if record.symbol in seen:
            raise InvalidMarketDataError(
                f"AKShare {source} source contains a duplicate symbol"
            )
        seen.add(record.symbol)
        result.append(record)
    return tuple(result)


__all__ = [
    "AkshareSecurityProvider",
    "SecurityMasterFallbackProvider",
    "SecurityMasterProvider",
    "SymbolProvider",
]
