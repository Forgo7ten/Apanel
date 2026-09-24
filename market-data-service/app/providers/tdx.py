"""TDX provider adapter with an injectable synchronous client boundary.

The project deliberately does not hard-code a third-party TDX package.  A
deployment can inject a client implementing :class:`TDXClientProtocol`, while
tests use a deterministic fake.  Every blocking client call runs in a worker
thread and is bounded by the adapter timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol, TypeVar

from app.domain.market_data import (
    Adjustment,
    DailyBar,
    Dividend,
    InvalidMarketDataError,
    Quote,
    Security,
    SecurityType,
    infer_market,
    normalize_adjustment,
    normalize_security_type,
    normalize_symbol,
)

from .base import MarketDataProvider
from .errors import (
    MarketDataProviderError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class TDXClientProtocol(Protocol):
    """Minimal blocking client protocol expected by :class:`TDXProvider`.

    The protocol intentionally describes adapter-facing operations rather than
    binding the application to a particular TDX SDK.  A real client remains
    responsible for connection setup and wire-format details.
    """

    def fetch_symbols(self) -> Sequence[Mapping[str, Any]]: ...

    def fetch_quote(self, symbol: str) -> Mapping[str, Any]: ...

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: Adjustment,
    ) -> Sequence[Mapping[str, Any]]: ...

    def fetch_dividends(self, symbol: str) -> Sequence[Mapping[str, Any]]: ...


TdxClientProtocol = TDXClientProtocol
ResultT = TypeVar("ResultT")


class TDXProvider(MarketDataProvider):
    """Normalize records returned by an injected blocking TDX client."""

    name = "tdx"

    def __init__(self, client: TDXClientProtocol | None, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._client = client
        self._timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        """Whether a real client was explicitly injected."""

        return self._client is not None

    async def aclose(self) -> None:
        """Close an injected client without running blocking code inline."""

        client = self._client
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is None:
            close = getattr(client, "disconnect", None)
        if close is None:
            return
        result = await asyncio.to_thread(close)
        if asyncio.iscoroutine(result):
            await result

    async def get_symbols(self) -> Sequence[Security]:
        raw_records = await self._call("symbols", self._require_client().fetch_symbols)
        return tuple(self._security_from_raw(record) for record in raw_records)

    async def get_quote(self, symbol: str) -> Quote:
        canonical_symbol = normalize_symbol(symbol)
        raw_record = await self._call(
            "quote",
            self._require_client().fetch_quote,
            canonical_symbol,
        )
        if not isinstance(raw_record, Mapping):
            raise InvalidMarketDataError("TDX quote must be a mapping")
        raw_symbol = _raw_symbol(raw_record, fallback=canonical_symbol)
        if normalize_symbol(raw_symbol) != canonical_symbol:
            raise InvalidMarketDataError(
                f"TDX quote symbol {raw_symbol!r} does not match {canonical_symbol!r}"
            )
        timestamp = _timestamp_value(raw_record.get("timestamp", raw_record.get("datetime")))
        return Quote(
            symbol=canonical_symbol,
            price=_required(raw_record, "price", "last", "close"),
            change=raw_record.get("change"),
            change_percent=raw_record.get("change_percent", raw_record.get("change_pct")),
            timestamp=timestamp,
        )

    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: Adjustment | str = Adjustment.QFQ,
    ) -> Sequence[DailyBar]:
        canonical_symbol = normalize_symbol(symbol)
        selected_adjustment = normalize_adjustment(adjustment)
        if not isinstance(start, date) or isinstance(start, datetime):
            raise InvalidMarketDataError("start must be a calendar date")
        if not isinstance(end, date) or isinstance(end, datetime):
            raise InvalidMarketDataError("end must be a calendar date")
        if start > end:
            raise InvalidMarketDataError("start must not be after end")
        raw_records = await self._call(
            "daily bars",
            self._require_client().fetch_daily_bars,
            canonical_symbol,
            start,
            end,
            selected_adjustment,
        )
        bars: list[DailyBar] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, Mapping):
                raise InvalidMarketDataError("TDX daily bar must be a mapping")
            raw_symbol = _raw_symbol(raw_record, fallback=canonical_symbol)
            if normalize_symbol(raw_symbol) != canonical_symbol:
                raise InvalidMarketDataError(
                    f"TDX daily bar symbol {raw_symbol!r} does not match {canonical_symbol!r}"
                )
            raw_adjustment = raw_record.get("adjustment")
            if (
                raw_adjustment is not None
                and normalize_adjustment(raw_adjustment) is not selected_adjustment
            ):
                raise InvalidMarketDataError("TDX daily bar adjustment does not match request")
            bars.append(
                DailyBar(
                    symbol=canonical_symbol,
                    trade_date=_date_value(
                        raw_record.get(
                            "trade_date",
                            raw_record.get("date", raw_record.get("datetime")),
                        )
                    ),
                    open=_required(raw_record, "open"),
                    high=_required(raw_record, "high"),
                    low=_required(raw_record, "low"),
                    close=_required(raw_record, "close"),
                    volume=_required(raw_record, "volume", "vol"),
                    amount=raw_record.get("amount", raw_record.get("turnover")),
                    adjustment=selected_adjustment,
                )
            )
        return tuple(bars)

    async def get_dividends(self, symbol: str) -> Sequence[Dividend]:
        canonical_symbol = normalize_symbol(symbol)
        raw_records = await self._call(
            "dividends",
            self._require_client().fetch_dividends,
            canonical_symbol,
        )
        dividends: list[Dividend] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, Mapping):
                raise InvalidMarketDataError("TDX dividend must be a mapping")
            raw_symbol = _raw_symbol(raw_record, fallback=canonical_symbol)
            if normalize_symbol(raw_symbol) != canonical_symbol:
                raise InvalidMarketDataError(
                    f"TDX dividend symbol {raw_symbol!r} does not match {canonical_symbol!r}"
                )
            dividends.append(
                Dividend(
                    symbol=canonical_symbol,
                    date=_date_value(raw_record.get("date", raw_record.get("ex_date"))),
                    cash_amount=_required(raw_record, "cash_amount", "cash"),
                )
            )
        return tuple(dividends)

    def _require_client(self) -> TDXClientProtocol:
        if self._client is None:
            raise ProviderConfigurationError(
                "TDX provider requires an injected client; network setup is outside the adapter"
            )
        return self._client

    async def _call(self, operation: str, method: Any, *args: Any) -> ResultT:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(method, *args),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"TDX {operation} timed out after {self._timeout_seconds:g}s"
            ) from exc
        except MarketDataProviderError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"TDX {operation} failed") from exc

    @staticmethod
    def _security_from_raw(raw_record: Mapping[str, Any]) -> Security:
        if not isinstance(raw_record, Mapping):
            raise InvalidMarketDataError("TDX security must be a mapping")
        symbol = _raw_symbol(raw_record)
        market = _tdx_market(raw_record.get("market"))
        resolved_market = infer_market(symbol) if market is None else market
        security_type = raw_record.get("security_type", raw_record.get("type"))
        if security_type is None:
            security_type = _infer_security_type(symbol)
        return Security(
            symbol=symbol,
            name=_required(raw_record, "name"),
            market=resolved_market,
            security_type=normalize_security_type(security_type),
            exchange=raw_record.get("exchange"),
        )


def _raw_symbol(record: Mapping[str, Any], fallback: str | None = None) -> str:
    value = record.get("symbol", record.get("code", fallback))
    if value is None:
        raise InvalidMarketDataError("TDX record is missing symbol/code")
    return str(value)


def _required(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    joined = "/".join(keys)
    raise InvalidMarketDataError(f"TDX record is missing {joined}")


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip().replace("/", "-")
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError as exc:
            raise InvalidMarketDataError("TDX date must be an ISO calendar date") from exc
    raise InvalidMarketDataError("TDX record is missing a valid date")


def _timestamp_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidMarketDataError("TDX timestamp must be ISO datetime") from exc
    raise InvalidMarketDataError("TDX quote is missing a timezone-aware timestamp")


def _infer_security_type(symbol: str) -> SecurityType:
    code = normalize_symbol(symbol)
    if code.startswith(("5", "15", "16", "50", "51", "56", "58", "159")):
        return SecurityType.ETF
    return SecurityType.STOCK


def _tdx_market(value: Any) -> Any:
    """Map common TDX numeric market ids before domain validation."""

    if value in (0, "0"):
        return "SZ"
    if value in (1, "1"):
        return "SH"
    if value in (2, "2"):
        return "BJ"
    if isinstance(value, str):
        normalized = value.strip().upper()
        return {
            "SHSE": "SH",
            "SSE": "SH",
            "SZSE": "SZ",
            "BSE": "BJ",
        }.get(normalized, normalized)
    return value
