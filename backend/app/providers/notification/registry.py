"""Registry for replaceable notification provider adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import NotificationProvider


ProviderBuilder = Callable[[str | None], "NotificationProvider"]


class NotificationProviderRegistry:
    """Resolve a notification provider without coupling services to adapters.

    Provider builders receive the user's configured destination.  Keeping that
    small factory seam in the provider package lets applications replace
    Feishu with another adapter without changing notification orchestration.
    """

    def __init__(self, providers: dict[str, ProviderBuilder] | None = None) -> None:
        self._providers: dict[str, ProviderBuilder] = {}
        for channel, builder in (providers or {}).items():
            self.register(channel, builder)

    def register(
        self,
        channel: str,
        builder: ProviderBuilder,
        *,
        replace: bool = False,
    ) -> None:
        normalized = _normalize_channel(channel)
        if not callable(builder):
            raise TypeError("notification provider builder must be callable")
        if normalized in self._providers and not replace:
            raise ValueError(f"notification provider already registered: {normalized}")
        self._providers[normalized] = builder

    def create(self, channel: str, destination: str | None) -> NotificationProvider:
        normalized = _normalize_channel(channel)
        try:
            builder = self._providers[normalized]
        except KeyError as exc:
            raise ValueError(f"notification provider is not registered: {normalized}") from exc
        return builder(destination)

    def channels(self) -> tuple[str, ...]:
        return tuple(self._providers)


def _normalize_channel(channel: str) -> str:
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("notification channel must be non-empty")
    return channel.strip().upper()


def _build_feishu(destination: str | None) -> NotificationProvider:
    # Keep the concrete import at the provider composition boundary.  Service
    # code depends only on NotificationProviderRegistry.
    from .feishu import FeishuProvider

    return FeishuProvider(destination)


DEFAULT_PROVIDER_REGISTRY = NotificationProviderRegistry({"FEISHU": _build_feishu})


def create_default_registry() -> NotificationProviderRegistry:
    """Return a fresh registry containing the built-in providers."""

    return NotificationProviderRegistry({"FEISHU": _build_feishu})


__all__ = [
    "DEFAULT_PROVIDER_REGISTRY",
    "NotificationProviderRegistry",
    "ProviderBuilder",
    "create_default_registry",
]
