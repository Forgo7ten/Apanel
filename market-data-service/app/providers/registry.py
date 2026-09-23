"""Provider registry and configuration-driven factory."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings

from .base import MarketDataProvider
from .tdx import TDXProvider
from .tdx_client import PytdxClient


class UnknownProviderError(LookupError):
    """Raised when configuration names an unregistered provider."""


ProviderFactory = Callable[..., MarketDataProvider]


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Non-persistent registry status suitable for diagnostics/API exposure."""

    name: str
    registered: bool


class ProviderRegistry:
    """A small replaceable registry with explicit provider names."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory, *, replace: bool = False) -> None:
        normalized_name = _provider_name(name)
        if normalized_name in self._factories and not replace:
            raise ValueError(f"provider already registered: {normalized_name}")
        self._factories[normalized_name] = factory

    def create(self, name: str, **kwargs: Any) -> MarketDataProvider:
        normalized_name = _provider_name(name)
        try:
            factory = self._factories[normalized_name]
        except KeyError as exc:
            raise UnknownProviderError(f"unknown provider: {name!r}") from exc
        provider = factory(**kwargs)
        if not isinstance(provider, MarketDataProvider):
            raise TypeError(f"provider factory {normalized_name!r} returned an invalid object")
        return provider

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def status(self, name: str) -> ProviderStatus:
        normalized_name = _provider_name(name)
        return ProviderStatus(
            name=normalized_name,
            registered=normalized_name in self._factories,
        )


def _provider_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnknownProviderError(f"provider name must not be empty: {value!r}")
    return value.strip().lower()


def _tdx_factory(
    *,
    client: Any = None,
    timeout_seconds: float = 5.0,
    servers: Any = None,
    connect_timeout_seconds: float | None = None,
    retry_attempts: int = 1,
    symbol_max_pages: int = 100,
    bar_page_size: int = 800,
    bar_max_pages: int = 64,
    **_: Any,
) -> TDXProvider:
    if client is None:
        client_kwargs = {
            "timeout_seconds": connect_timeout_seconds or timeout_seconds,
            "retry_attempts": retry_attempts,
            "symbol_max_pages": symbol_max_pages,
            "bar_page_size": bar_page_size,
            "bar_max_pages": bar_max_pages,
        }
        if servers is not None:
            client_kwargs["servers"] = servers
        client = PytdxClient(**client_kwargs)
    return TDXProvider(client, timeout_seconds=timeout_seconds)


DEFAULT_PROVIDER_REGISTRY = ProviderRegistry()
DEFAULT_PROVIDER_REGISTRY.register("tdx", _tdx_factory)


def create_provider(
    settings: Settings | str | None = None,
    *,
    registry: ProviderRegistry = DEFAULT_PROVIDER_REGISTRY,
    **kwargs: Any,
) -> MarketDataProvider:
    """Create the configured provider without opening a network connection."""

    if isinstance(settings, str):
        provider_name = settings
        kwargs.setdefault("timeout_seconds", 5.0)
    else:
        selected_settings = settings or get_settings()
        provider_name = selected_settings.market_data_provider
        kwargs.setdefault("timeout_seconds", selected_settings.provider_timeout_seconds)
        if _provider_name(provider_name) == "tdx":
            kwargs.setdefault("servers", selected_settings.tdx_servers)
            kwargs.setdefault(
                "connect_timeout_seconds", selected_settings.tdx_connect_timeout_seconds
            )
            kwargs.setdefault("retry_attempts", selected_settings.tdx_retry_attempts)
            kwargs.setdefault("symbol_max_pages", selected_settings.tdx_symbol_max_pages)
            kwargs.setdefault("bar_page_size", selected_settings.tdx_bar_page_size)
            kwargs.setdefault("bar_max_pages", selected_settings.tdx_bar_max_pages)
    return registry.create(provider_name, **kwargs)


get_provider = create_provider


async def close_provider(provider: MarketDataProvider | None) -> None:
    """Close a provider resource using its async or blocking close seam."""

    if provider is None:
        return
    close = getattr(provider, "aclose", None)
    if close is None:
        close = getattr(provider, "close", None)
    if close is None:
        return
    if inspect.iscoroutinefunction(close):
        await close()
        return
    result = await asyncio.to_thread(close)
    if inspect.isawaitable(result):
        await result
