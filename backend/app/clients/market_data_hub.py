"""Backend adapter for the authenticated Market Data Hub boundary."""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

_RETRYABLE_ERROR_CODES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PERSISTENCE_ERROR",
    }
)
_RETRYABLE_AGGREGATE_ERROR_CODES = frozenset({"PARTIAL_SYNC_FAILURE"})


class MarketDataHubClientError(RuntimeError):
    """A market-data request failed with a safe public description.

    The original exception detail is intentionally not retained in the public
    message.  Callers can use ``retryable`` to make a policy decision without
    parsing text that may contain response or credential data.
    """

    retryable = False
    public_message = "market data client failed"

    def __init__(self, detail: str | None = None) -> None:
        del detail
        super().__init__(self.public_message)


class RetryableMarketDataHubError(MarketDataHubClientError):
    """A bounded request failure that may succeed on a later attempt."""

    retryable = True
    public_message = "market data request temporarily unavailable"


class PermanentMarketDataHubError(MarketDataHubClientError):
    """A response or protocol failure that must not be retried."""

    public_message = "market data synchronization failed"


@dataclass(frozen=True, slots=True)
class SyncItem:
    symbol: str
    status: str
    fetched: int = 0
    persisted: int = 0
    error_code: str | None = None
    history_rebased: bool = False
    changed_from: date | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


@dataclass(frozen=True, slots=True)
class SyncResult(Mapping[str, Any]):
    operation: str
    items: tuple[SyncItem, ...]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(item.succeeded for item in self.items)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def retryable_symbols(self) -> tuple[str, ...]:
        return tuple(
            item.symbol for item in self.items if item.error_code in _RETRYABLE_ERROR_CODES
        )

    @property
    def permanent_failures(self) -> tuple[str, ...]:
        return tuple(
            item.symbol
            for item in self.items
            if not item.succeeded and item.error_code not in _RETRYABLE_ERROR_CODES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "ok": self.ok,
            "items": [
                {
                    "symbol": item.symbol,
                    "status": item.status,
                    "fetched": item.fetched,
                    "persisted": item.persisted,
                    "error": ({"code": item.error_code} if item.error_code else None),
                    "history_rebased": item.history_rebased,
                    "changed_from": item.changed_from.isoformat() if item.changed_from else None,
                }
                for item in self.items
            ],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("operation", "total", "succeeded", "failed", "ok", "items"))

    def __len__(self) -> int:
        return 6


class MarketDataHubClientProtocol(Protocol):
    async def sync_securities(self) -> Mapping[str, Any]: ...

    async def sync_daily(
        self,
        *,
        symbols: Iterable[str],
        start: date | None = None,
        end: date | None = None,
        lookback_bars: int | None = None,
        adjustment: str,
    ) -> SyncResult: ...

    async def sync_quotes(self, *, symbols: Iterable[str]) -> SyncResult: ...
    async def sync_dividends(self, *, symbols: Iterable[str]) -> SyncResult: ...
    async def validate_calendar(self, *, trade_date: date) -> Mapping[str, Any]: ...


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("market data service URL is required")
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("market data service URL is invalid")
    return normalized


class MarketDataHubClient:
    """Call only the stable internal sync API; HTTP is injectable for tests."""

    def __init__(
        self,
        base_url: str,
        *,
        internal_api_token: str | None = None,
        timeout_seconds: float = 10.0,
        quote_timeout_seconds: float | None = None,
        daily_timeout_seconds: float | None = None,
        bootstrap_timeout_seconds: float | None = None,
        client: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = _validate_base_url(base_url)
        self.internal_api_token = internal_api_token.strip() if internal_api_token else None
        self.timeout_seconds = float(timeout_seconds)
        self.quote_timeout_seconds = float(quote_timeout_seconds or timeout_seconds)
        self.daily_timeout_seconds = float(daily_timeout_seconds or timeout_seconds)
        self.bootstrap_timeout_seconds = float(
            bootstrap_timeout_seconds or self.daily_timeout_seconds
        )
        self._client = client
        self._owns_client = client is None

    async def sync_securities(self) -> Mapping[str, Any]:
        return await self._post("/internal/sync/securities", {})

    async def sync_daily(
        self,
        *,
        symbols: Iterable[str],
        start: date | None = None,
        end: date | None = None,
        lookback_bars: int | None = None,
        adjustment: str,
    ) -> SyncResult:
        payload: dict[str, Any] = {"symbols": list(symbols), "adjustment": adjustment}
        if lookback_bars is not None:
            payload["lookback_bars"] = int(lookback_bars)
            timeout = self.bootstrap_timeout_seconds
        else:
            if start is None or end is None:
                raise ValueError("start/end are required outside bootstrap mode")
            payload.update({"start": start.isoformat(), "end": end.isoformat()})
            timeout = self.daily_timeout_seconds
        return _parse_sync_result(
            await self._post(
                "/internal/sync/daily", payload, allow_partial=True, timeout_seconds=timeout
            )
        )

    async def sync_quotes(self, *, symbols: Iterable[str]) -> SyncResult:
        return _parse_sync_result(
            await self._post(
                "/internal/sync/quotes",
                {"symbols": list(symbols)},
                allow_partial=True,
                timeout_seconds=self.quote_timeout_seconds,
            )
        )

    async def sync_dividends(self, *, symbols: Iterable[str]) -> SyncResult:
        return _parse_sync_result(
            await self._post(
                "/internal/sync/dividends",
                {"symbols": list(symbols)},
                allow_partial=True,
                timeout_seconds=self.daily_timeout_seconds,
            )
        )

    async def validate_calendar(self, *, trade_date: date) -> Mapping[str, Any]:
        return await self._post(
            "/internal/calendar/validate",
            {"trade_date": trade_date.isoformat()},
            timeout_seconds=self.daily_timeout_seconds,
        )

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        allow_partial: bool = False,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))
        headers = {}
        if self.internal_api_token:
            headers["X-Internal-Token"] = self.internal_api_token
        try:
            response = await self._client.post(
                f"{self.base_url}{path}",
                json=dict(payload),
                headers=headers,
                timeout=timeout_seconds or self.timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError, ConnectionError) as exc:
            raise RetryableMarketDataHubError from exc
        except httpx.RequestError as exc:
            raise RetryableMarketDataHubError from exc

        status_code = getattr(response, "status_code", None)
        if type(status_code) is not int or not 200 <= status_code < 300:
            if type(status_code) is int and (
                status_code in {408, 429} or 500 <= status_code <= 599
            ):
                raise RetryableMarketDataHubError
            raise PermanentMarketDataHubError
        try:
            body = response.json()
            if inspect.isawaitable(body):
                body = await body
        except Exception as exc:
            raise PermanentMarketDataHubError from exc
        if not isinstance(body, Mapping):
            raise PermanentMarketDataHubError
        if body.get("success") is not True:
            if allow_partial and body.get("success") is False and _is_partial_sync_envelope(body):
                data = body.get("data")
                if not isinstance(data, Mapping):
                    raise PermanentMarketDataHubError
                return data
            if body.get("success") is False and _has_retryable_error_code(body):
                raise RetryableMarketDataHubError
            raise PermanentMarketDataHubError
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise PermanentMarketDataHubError
        return data

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def _is_partial_sync_envelope(body: Mapping[str, Any]) -> bool:
    error = body.get("error")
    return isinstance(error, Mapping) and error.get("code") == "PARTIAL_SYNC_FAILURE"


def _parse_sync_result(data: Mapping[str, Any]) -> SyncResult:
    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        raise PermanentMarketDataHubError
    items: list[SyncItem] = []
    for raw in items_raw:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("symbol"), str):
            raise PermanentMarketDataHubError
        error = raw.get("error")
        code = _error_code(error) if error is not None else None
        changed_from = raw.get("changed_from")
        try:
            changed_date = (
                date.fromisoformat(changed_from) if isinstance(changed_from, str) else None
            )
        except ValueError as exc:
            raise PermanentMarketDataHubError from exc
        items.append(
            SyncItem(
                symbol=raw["symbol"],
                status=str(raw.get("status", "failed")),
                fetched=int(raw.get("fetched", 0)),
                persisted=int(raw.get("persisted", 0)),
                error_code=code,
                history_rebased=bool(raw.get("history_rebased", False)),
                changed_from=changed_date,
            )
        )
    return SyncResult(operation=str(data.get("operation", "unknown")), items=tuple(items))


def _has_retryable_error_code(body: Mapping[str, Any]) -> bool:
    """Recognize a transient failure only when the whole envelope agrees."""

    top_level_is_valid, top_level_code = _top_level_error_code(body)
    if not top_level_is_valid:
        return False

    item_result = _item_error_classification(body.get("data"))
    if item_result is False:
        return False

    if top_level_code is None:
        return item_result is True

    if top_level_code in _RETRYABLE_AGGREGATE_ERROR_CODES:
        return item_result is True

    if top_level_code not in _RETRYABLE_ERROR_CODES:
        return False

    # A direct provider error is valid without item details, but an item list
    # must still contain only retryable provider failures.
    return body.get("data") is None or item_result is True


def _top_level_error_code(body: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Return the top-level error code, rejecting malformed error objects."""

    if "error" not in body or body.get("error") is None:
        return True, None
    error = body.get("error")
    if not isinstance(error, Mapping) or "code" not in error:
        return False, None
    code = error.get("code")
    if type(code) is not str:
        return False, None
    return True, code


def _item_error_classification(data: object) -> bool | None:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        return False
    if "items" not in data:
        return False
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return False

    error_codes: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            return False
        if "error" not in item:
            return False
        error = item.get("error")
        if error is None:
            continue
        code = _error_code(error)
        if code is None:
            return False
        error_codes.append(code)
    if not error_codes:
        return False
    return all(code in _RETRYABLE_ERROR_CODES for code in error_codes)


def _error_code(error: object) -> str | None:
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    if type(code) is not str:
        return None
    return code


__all__ = [
    "MarketDataHubClient",
    "MarketDataHubClientProtocol",
    "MarketDataHubClientError",
    "PermanentMarketDataHubError",
    "RetryableMarketDataHubError",
    "SyncItem",
    "SyncResult",
]
