"""Backend adapter for the authenticated market-data service boundary."""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


class MarketDataClientError(RuntimeError):
    """A market-data request failed with a safe public description."""


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
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise MarketDataClientError("market data request timed out") from exc
        except httpx.RequestError as exc:
            raise MarketDataClientError("market data request failed") from exc
        except Exception as exc:  # fake transports may expose another exception type
            raise MarketDataClientError("market data request failed") from exc

        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            raise MarketDataClientError("market data service returned an error")
        try:
            body = response.json()
            if inspect.isawaitable(body):
                body = await body
        except Exception as exc:
            raise MarketDataClientError("market data response was invalid") from exc
        if not isinstance(body, Mapping) or body.get("success") is not True:
            raise MarketDataClientError("market data synchronization failed")
        data = body.get("data")
        if not isinstance(data, Mapping):
            raise MarketDataClientError("market data response was invalid")
        return data

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["HttpMarketDataClient", "MarketDataClient", "MarketDataClientError"]
