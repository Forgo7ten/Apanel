"""Backend adapter for the authenticated market-data service boundary."""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

_RETRYABLE_ERROR_CODES = frozenset(
    {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
    }
)
_RETRYABLE_AGGREGATE_ERROR_CODES = frozenset({"PARTIAL_SYNC_FAILURE"})


class MarketDataClientError(RuntimeError):
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


class RetryableMarketDataClientError(MarketDataClientError):
    """A bounded request failure that may succeed on a later attempt."""

    retryable = True
    public_message = "market data request temporarily unavailable"


class PermanentMarketDataClientError(MarketDataClientError):
    """A response or protocol failure that must not be retried."""

    public_message = "market data synchronization failed"


class MarketDataClient(Protocol):
    async def sync_securities(self) -> Mapping[str, Any]: ...

    async def sync_daily(
        self,
        *,
        symbols: Iterable[str],
        start: date,
        end: date,
        adjustment: str,
    ) -> Mapping[str, Any]: ...

    async def sync_quotes(self, *, symbols: Iterable[str]) -> Mapping[str, Any]: ...


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("market data service URL is required")
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("market data service URL is invalid")
    return normalized


class HttpMarketDataClient:
    """Call only the stable internal sync API; HTTP is injectable for tests."""

    def __init__(
        self,
        base_url: str,
        *,
        internal_api_token: str | None = None,
        timeout_seconds: float = 10.0,
        client: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = _validate_base_url(base_url)
        self.internal_api_token = internal_api_token.strip() if internal_api_token else None
        self.timeout_seconds = float(timeout_seconds)
        self._client = client
        self._owns_client = client is None

    async def sync_securities(self) -> Mapping[str, Any]:
        return await self._post("/internal/sync/securities", {})

    async def sync_daily(
        self,
        *,
        symbols: Iterable[str],
        start: date,
        end: date,
        adjustment: str,
    ) -> Mapping[str, Any]:
        return await self._post(
            "/internal/sync/daily",
            {
                "symbols": list(symbols),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "adjustment": adjustment,
            },
        )

    async def sync_quotes(self, *, symbols: Iterable[str]) -> Mapping[str, Any]:
        return await self._post("/internal/sync/quotes", {"symbols": list(symbols)})

    async def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
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
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError, ConnectionError) as exc:
            raise RetryableMarketDataClientError from exc
        except httpx.RequestError as exc:
            raise RetryableMarketDataClientError from exc

        status_code = getattr(response, "status_code", None)
        if type(status_code) is not int or not 200 <= status_code < 300:
            if type(status_code) is int and (
                status_code in {408, 429} or 500 <= status_code <= 599
            ):
                raise RetryableMarketDataClientError
            raise PermanentMarketDataClientError
        try:
            body = response.json()
            if inspect.isawaitable(body):
                body = await body
        except Exception as exc:
            raise PermanentMarketDataClientError from exc
        if not isinstance(body, Mapping):
            raise PermanentMarketDataClientError
        if body.get("success") is not True:
            if body.get("success") is False and _has_retryable_error_code(body):
                raise RetryableMarketDataClientError
            raise PermanentMarketDataClientError
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise PermanentMarketDataClientError
        return data

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


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
    "HttpMarketDataClient",
    "MarketDataClient",
    "MarketDataClientError",
    "PermanentMarketDataClientError",
    "RetryableMarketDataClientError",
]
