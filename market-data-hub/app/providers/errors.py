"""Stable errors emitted by provider adapters and sync services."""

from __future__ import annotations


class MarketDataProviderError(RuntimeError):
    """Base class for operational provider failures."""


class ProviderConfigurationError(MarketDataProviderError):
    """Raised when an adapter has no injected client/configuration."""


class ProviderTimeoutError(MarketDataProviderError):
    """Raised when a bounded provider operation exceeds its timeout."""


class ProviderUnavailableError(MarketDataProviderError):
    """Raised when a provider client cannot complete an operation."""


class ProviderUnsupportedError(MarketDataProviderError):
    """Raised when a provider cannot provide a requested capability."""
