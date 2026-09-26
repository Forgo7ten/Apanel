"""eltdx-backed TDX provider for the Market Data Hub."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, TypeVar

from app.domain.market_data import (
    Adjustment,
    DailyBar,
    Dividend,
    InvalidMarketDataError,
    Market,
    Quote,
    Security,
    SecurityType,
    infer_market,
    normalize_adjustment,
    normalize_symbol,
)

from .errors import ProviderTimeoutError, ProviderUnavailableError

try:  # Imported eagerly in production; kept isolated for adapter tests/fakes.
    from eltdx.exceptions import PoolBusyError, ResponseTimeoutError, TransportError
except ImportError:  # pragma: no cover - dependency is required in deployed runtime
    PoolBusyError = ResponseTimeoutError = TransportError = ()  # type: ignore[assignment]


ResultT = TypeVar("ResultT")


class EltdxProvider:
    """Translate eltdx models into Apanel's canonical market-data domain."""

    name = "eltdx"

    def __init__(
        self,
        client: Any,
        *,
        bar_page_size: int = 800,
        bar_max_pages: int = 64,
    ) -> None:
        if client is None:
            raise ValueError("eltdx client is required")
        if bar_page_size <= 0 or bar_page_size > 800:
            raise ValueError("bar_page_size must be between 1 and 800")
        if bar_max_pages <= 0:
            raise ValueError("bar_max_pages must be positive")
        self._client = client
        self._bar_page_size = int(bar_page_size)
        self._bar_max_pages = int(bar_max_pages)
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)

    async def get_symbols(self) -> Sequence[Security]:
        rows = await self._call("security master", self._fetch_symbols)
        return tuple(rows)

    def _fetch_symbols(self) -> list[Security]:
        result: list[Security] = []
        seen: dict[str, Security] = {}
        for market in ("sh", "sz", "bj"):
            rows = self._client.codes.all(market)
            for row in rows or ():
                category = str(getattr(row, "category", ""))
                if category == "a_share":
                    security_type = SecurityType.STOCK
                elif category == "etf":
                    security_type = SecurityType.ETF
                else:
                    continue
                code = str(getattr(row, "code", "")).strip()
                name = str(getattr(row, "name", "")).strip()
                exchange = str(getattr(row, "exchange", market)).strip().upper()
                if not code or not name:
                    raise InvalidMarketDataError("eltdx security row is incomplete")
                security = Security(
                    symbol=code,
                    name=name,
                    market=Market(exchange),
                    security_type=security_type,
                    exchange=exchange,
                )
                previous = seen.get(security.symbol)
                if previous is not None:
                    if previous != security:
                        raise InvalidMarketDataError(
                            "eltdx security master contains conflicting metadata"
                        )
                    continue
                seen[security.symbol] = security
                result.append(security)
        if not result:
            raise ProviderUnavailableError("eltdx security master returned no supported records")
        return result

    async def get_quote(self, symbol: str) -> Quote:
        canonical = normalize_symbol(symbol)
        full_code = _full_code(canonical)
        observed_at = datetime.now(UTC)
        rows = await self._call(
            "quote",
            self._client.quotes.get_snapshots,
            [full_code],
        )
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
            raise ProviderUnavailableError("eltdx quote returned no record")
        row = rows[0]
        row_code = str(getattr(row, "code", "")).strip()
        if normalize_symbol(row_code) != canonical:
            raise InvalidMarketDataError("eltdx quote symbol does not match request")
        return Quote(
            symbol=canonical,
            price=row.last_price,
            change=getattr(row, "change", None),
            change_percent=getattr(row, "change_pct", None),
            timestamp=observed_at,
        )

    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: Adjustment | str = Adjustment.QFQ,
    ) -> Sequence[DailyBar]:
        canonical = normalize_symbol(symbol)
        selected = normalize_adjustment(adjustment)
        if start > end:
            raise InvalidMarketDataError("start must not be after end")
        series = await self._call(
            "daily bars",
            self._client.bars.get,
            _full_code(canonical),
            period="day",
            adjust=selected.value,
            all_pages=True,
            page_size=self._bar_page_size,
            max_pages=self._bar_max_pages,
        )
        adjust_mode = str(getattr(series, "adjust_mode", "")).strip().lower()
        if adjust_mode and adjust_mode != selected.value:
            raise InvalidMarketDataError("eltdx daily-bar adjustment does not match request")
        rows: list[DailyBar] = []
        for bar in getattr(series, "bars", ()) or ():
            timestamp = getattr(bar, "time", None)
            if not isinstance(timestamp, datetime):
                raise InvalidMarketDataError("eltdx daily bar is missing a datetime")
            trade_date = timestamp.date()
            if trade_date < start or trade_date > end:
                continue
            rows.append(
                DailyBar(
                    symbol=canonical,
                    trade_date=trade_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume_lots,
                    amount=getattr(bar, "amount", None),
                    adjustment=selected,
                )
            )
        rows.sort(key=lambda item: item.trade_date)
        return tuple(rows)

    async def get_dividends(self, symbol: str) -> Sequence[Dividend]:
        canonical = normalize_symbol(symbol)
        response = await self._call(
            "dividends",
            self._client.corporate.capital_changes,
            _full_code(canonical),
        )
        totals: defaultdict[date, Decimal] = defaultdict(lambda: Decimal("0"))
        for record in getattr(response, "records", ()) or ():
            if int(getattr(record, "category", -1)) != 1:
                continue
            event_date = getattr(record, "date", None)
            if not isinstance(event_date, date):
                continue
            cash_per_ten = Decimal(str(getattr(record, "c1_value", 0)))
            cash_per_share = cash_per_ten / Decimal("10")
            if cash_per_share > 0:
                totals[event_date] += cash_per_share
        return tuple(
            Dividend(symbol=canonical, date=event_date, cash_amount=amount)
            for event_date, amount in sorted(totals.items())
        )

    async def _call(self, operation: str, method: Any, *args: Any, **kwargs: Any) -> ResultT:
        if self._closed:
            raise ProviderUnavailableError("eltdx provider is closed")
        try:
            return await asyncio.to_thread(method, *args, **kwargs)
        except Exception as exc:
            if _matches_exception(exc, ResponseTimeoutError):
                raise ProviderTimeoutError(f"eltdx {operation} timed out") from exc
            if _matches_exception(exc, (PoolBusyError, TransportError)):
                raise ProviderUnavailableError(f"eltdx {operation} is unavailable") from exc
            if isinstance(exc, (InvalidMarketDataError, ProviderUnavailableError)):
                raise
            raise ProviderUnavailableError(f"eltdx {operation} failed") from exc


def _matches_exception(exc: Exception, classes: object) -> bool:
    if classes == ():
        return False
    try:
        return isinstance(exc, classes)  # type: ignore[arg-type]
    except TypeError:
        return False


def _full_code(symbol: str) -> str:
    market = infer_market(symbol)
    prefix = {Market.SH: "sh", Market.SZ: "sz", Market.BJ: "bj"}[market]
    return f"{prefix}{normalize_symbol(symbol)}"


__all__ = ["EltdxProvider"]
