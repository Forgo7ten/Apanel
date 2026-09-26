"""TDX provider adapter with an injectable synchronous client boundary.

The project deliberately does not hard-code a third-party TDX package.  A
deployment can inject a client implementing :class:`TDXClientProtocol`, while
tests use a deterministic fake.  Every blocking client call runs in a worker
thread and is bounded by the adapter timeout.
"""

from __future__ import annotations

import asyncio
import re
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
    UnknownMarketError,
    infer_market,
    normalize_adjustment,
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


# These are deliberately narrow families.  In particular, broad ``5``/``15``
#/``16`` heuristics also capture bonds, LOFs, closed-end funds and indices.
_STOCK_CODE_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "430",
    "600",
    "601",
    "603",
    "605",
    "688",
    "689",
    "830",
    "831",
    "832",
    "833",
    "834",
    "835",
    "836",
    "837",
    "838",
    "839",
    "870",
    "871",
    "872",
    "873",
    "920",
)
_ETF_CODE_PREFIXES = (
    "159",
    "510",
    "511",
    "512",
    "513",
    "515",
    "516",
    "517",
    "518",
    "519",
    "560",
    "561",
    "562",
    "563",
    "588",
)
_UNSUPPORTED_RAW_TYPE = object()


class TDXProvider(MarketDataProvider):
    """Normalize records returned by an injected blocking TDX client."""

    name = "tdx"

    def __init__(
        self,
        client: TDXClientProtocol | None,
        *,
        timeout_seconds: float = 5.0,
        symbol_timeout_seconds: float = 60.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if symbol_timeout_seconds <= 0:
            raise ValueError("symbol_timeout_seconds must be greater than zero")
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._symbol_timeout_seconds = symbol_timeout_seconds

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
        raw_records = await self._call(
            "symbols",
            self._require_client().fetch_symbols,
            timeout=self._symbol_timeout_seconds,
        )
        if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes, Mapping)):
            raise InvalidMarketDataError("TDX symbols response must be a sequence")
        return tuple(
            security
            for raw_record in raw_records
            if (security := self._security_from_raw(raw_record)) is not None
        )

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

    async def _call(
        self,
        operation: str,
        method: Any,
        *args: Any,
        timeout: float | None = None,
    ) -> ResultT:
        selected_timeout = self._timeout_seconds if timeout is None else timeout
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(method, *args),
                timeout=selected_timeout,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"TDX {operation} timed out after {selected_timeout:g}s"
            ) from exc
        except MarketDataProviderError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(f"TDX {operation} failed") from exc

    @staticmethod
    def _security_from_raw(raw_record: Mapping[str, Any]) -> Security | None:
        if not isinstance(raw_record, Mapping):
            raise InvalidMarketDataError("TDX security must be a mapping")
        symbol = _raw_symbol(raw_record)
        if _code_security_type(_classification_code(symbol)) is None:
            return None
        name = _required(raw_record, "name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidMarketDataError("TDX security name must be a non-empty string")
        security_type = _classify_security_type(
            symbol,
            name,
            _combined_raw_security_type(raw_record),
        )
        if security_type is None:
            return None
        resolved_market = infer_market(symbol)
        return Security(
            symbol=symbol,
            name=_required(raw_record, "name"),
            market=resolved_market,
            security_type=security_type,
            exchange=resolved_market.value,
        )


def _raw_symbol(record: Mapping[str, Any], fallback: str | None = None) -> str:
    for key in ("symbol", "code"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    if fallback is not None and str(fallback).strip():
        return str(fallback)
    raise InvalidMarketDataError("TDX record is missing symbol/code")


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


def _classify_security_type(
    symbol: str,
    name: str,
    raw_type: Any,
) -> SecurityType | None:
    """Conservatively classify only ordinary A-shares and ETFs.

    A known code family is the primary signal.  Explicit raw type values can
    reject ambiguous records or expose a type conflict, while unsupported
    categories are skipped before they can become a false STOCK default.
    """

    code = _classification_code(symbol)
    code_type = _code_security_type(code)
    raw_category = _raw_security_type(raw_type)
    if raw_category is _UNSUPPORTED_RAW_TYPE:
        return None

    name_category = _name_security_type(name)
    if name_category is _UNSUPPORTED_RAW_TYPE:
        return None

    if raw_category is not None and code_type is not None and raw_category is not code_type:
        raise InvalidMarketDataError(
            f"security type conflict for {code}: code implies {code_type.value}, "
            f"raw type implies {raw_category.value}"
        )
    if name_category is SecurityType.ETF and code_type is SecurityType.STOCK:
        raise InvalidMarketDataError(f"security type conflict for {code}: name implies ETF")
    if name_category is SecurityType.ETF and raw_category is SecurityType.STOCK:
        raise InvalidMarketDataError(f"security type conflict for {code}: name implies ETF")

    # An unknown code family is never promoted to STOCK from a missing or
    # unfamiliar provider type.  This keeps unsupported funds/indices out of
    # the v1 universe even when a feed changes its labels.
    if code_type is None:
        return None
    if name_category is SecurityType.ETF and code_type is SecurityType.ETF:
        return SecurityType.ETF
    return code_type


def _code_security_type(code: str) -> SecurityType | None:
    if code.startswith(_ETF_CODE_PREFIXES):
        return SecurityType.ETF
    if code.startswith(_STOCK_CODE_PREFIXES):
        return SecurityType.STOCK
    return None


def _classification_code(symbol: str) -> str:
    try:
        return normalize_symbol(symbol)
    except UnknownMarketError:
        candidate = str(symbol).strip().upper()
        match = re.fullmatch(r"(?:SH|SZ|BJ)[.:]?(\d{6})|(\d{6})[.:](?:SH|SZ|BJ)|(\d{6})", candidate)
        if match is None:
            raise
        return next(group for group in match.groups() if group is not None)


def _raw_security_type(value: Any) -> SecurityType | object | None:
    if value is None:
        return None
    if isinstance(value, SecurityType):
        return value
    if not isinstance(value, str):
        return _UNSUPPORTED_RAW_TYPE
    normalized = value.strip().upper().replace("-", "_").replace(" ", "")
    if normalized in {
        "STOCK",
        "A_SHARE",
        "ASHARE",
        "A股",
        "股票",
        "普通股",
        "普通股票",
    }:
        return SecurityType.STOCK
    if normalized in {"ETF", "ETF基金", "交易型开放式指数基金"}:
        return SecurityType.ETF
    if any(
        marker in normalized
        for marker in (
            "INDEX",
            "指数",
            "BOND",
            "债券",
            "B_SHARE",
            "B股",
            "LOF",
            "FUND",
            "基金",
        )
    ):
        return _UNSUPPORTED_RAW_TYPE
    return _UNSUPPORTED_RAW_TYPE


def _combined_raw_security_type(record: Mapping[str, Any]) -> Any:
    """Reject contradictory normalized and wire type fields."""

    values = [record[key] for key in ("security_type", "type") if record.get(key) is not None]
    if len(values) < 2:
        return values[0] if values else None
    categories = [_raw_security_type(value) for value in values]
    first = categories[0]
    if any(category != first for category in categories[1:]):
        raise InvalidMarketDataError("security type conflict between raw type fields")
    return values[0]


def _name_security_type(name: str) -> SecurityType | object | None:
    normalized = name.strip().upper()
    if "ETF" in normalized or "交易型开放式指数基金" in normalized:
        return SecurityType.ETF
    if any(marker in normalized for marker in ("LOF", "B股", "债券", "指数", "基金")):
        return _UNSUPPORTED_RAW_TYPE
    return None


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
