"""Market Data Hub provider construction and lifecycle."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings

from .akshare import AkshareSecurityProvider, SecurityMasterFallbackProvider
from .contracts import DailyBarProvider, DividendProvider, QuoteProvider, SecurityMasterProvider
from .eltdx import EltdxProvider


class UnknownProviderError(LookupError):
    """Raised when configuration names an unsupported provider."""


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    registered: bool


class ProviderRegistry:
    """Registry of explicitly constructed v1 providers and their capabilities."""

    def __init__(self) -> None:
        self._providers: dict[str, object] = {}

    def register(self, name: str, provider: object, *, replace: bool = False) -> None:
        normalized = _provider_name(name)
        if normalized in self._providers and not replace:
            raise ValueError(f"provider already registered: {normalized}")
        capabilities = (SecurityMasterProvider, QuoteProvider, DailyBarProvider, DividendProvider)
        if not any(isinstance(provider, capability) for capability in capabilities):
            raise TypeError(f"provider {normalized!r} has no supported market-data capability")
        self._providers[normalized] = provider

    def get(self, name: str) -> object:
        normalized = _provider_name(name)
        try:
            return self._providers[normalized]
        except KeyError as exc:
            raise UnknownProviderError(f"unknown provider: {name!r}") from exc

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def status(self, name: str) -> ProviderStatus:
        normalized = _provider_name(name)
        return ProviderStatus(name=normalized, registered=normalized in self._providers)


DEFAULT_PROVIDER_REGISTRY = ProviderRegistry()


def create_provider(settings: Settings | None = None, **_: Any) -> EltdxProvider:
    """Construct and connect the process-scoped eltdx primary provider."""

    selected = settings or get_settings()
    try:
        from eltdx import TdxClient
    except ImportError as exc:  # pragma: no cover - deployment dependency check
        raise RuntimeError("eltdx is required by the Market Data Hub") from exc

    kwargs: dict[str, Any] = {
        "timeout": selected.eltdx_timeout_seconds,
        "probe_hosts": selected.eltdx_probe_hosts,
        "heartbeat_interval": selected.eltdx_heartbeat_interval_seconds,
    }
    if selected.eltdx_hosts:
        kwargs["hosts"] = list(selected.eltdx_hosts)
    if selected.eltdx_pool_size is not None:
        kwargs["pool_size"] = selected.eltdx_pool_size
    if selected.eltdx_server_count is not None:
        kwargs["server_count"] = selected.eltdx_server_count
    if selected.eltdx_connections_per_server is not None:
        kwargs["connections_per_server"] = selected.eltdx_connections_per_server

    client = TdxClient(**kwargs)
    client.connect()
    return EltdxProvider(
        client,
        bar_page_size=selected.eltdx_bar_page_size,
        bar_max_pages=selected.eltdx_bar_max_pages,
    )


def create_security_master_provider(
    settings: Settings | None = None,
    *,
    primary: SecurityMasterProvider,
    **kwargs: Any,
) -> SecurityMasterProvider:
    """Compose eltdx security metadata with the existing AKShare fallback."""

    selected = settings or get_settings()
    fallback_name = _provider_name(selected.security_master_fallback_provider)
    if fallback_name in {"none", "disabled", "off", "false"}:
        return primary
    if fallback_name != "akshare":
        raise UnknownProviderError(f"unknown security master fallback provider: {fallback_name!r}")
    fallback = AkshareSecurityProvider(
        timeout_seconds=kwargs.pop("timeout_seconds", selected.akshare_security_timeout_seconds),
        min_stock_count=kwargs.pop("min_stock_count", selected.akshare_min_stock_count),
        min_etf_count=kwargs.pop("min_etf_count", selected.akshare_min_etf_count),
        stock_fetcher=kwargs.pop("stock_fetcher", None),
        etf_fetcher=kwargs.pop("etf_fetcher", None),
    )
    if kwargs:
        unknown = ", ".join(sorted(kwargs))
        raise TypeError(f"unexpected security master provider options: {unknown}")
    return SecurityMasterFallbackProvider(primary=primary, fallback=fallback)


def get_provider(settings: Settings | None = None) -> EltdxProvider:
    return create_provider(settings)


async def close_provider(provider: object | None) -> None:
    if provider is None:
        return
    close = getattr(provider, "aclose", None) or getattr(provider, "close", None)
    if close is None:
        return
    if inspect.iscoroutinefunction(close):
        await close()
        return
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        await result


def _provider_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnknownProviderError(f"provider name must not be empty: {value!r}")
    return value.strip().lower()


__all__ = [
    "DEFAULT_PROVIDER_REGISTRY",
    "ProviderRegistry",
    "ProviderStatus",
    "UnknownProviderError",
    "close_provider",
    "create_provider",
    "create_security_master_provider",
    "get_provider",
]
