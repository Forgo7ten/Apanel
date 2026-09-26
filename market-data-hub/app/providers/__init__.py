"""Market Data Hub provider contracts, routing, and concrete adapters."""

from .akshare import AkshareSecurityProvider, SecurityMasterFallbackProvider
from .contracts import (
    DailyBarProvider,
    DividendProvider,
    QuoteProvider,
    SecurityMasterProvider,
    SymbolProvider,
)
from .eltdx import EltdxProvider
from .errors import (
    MarketDataProviderError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUnsupportedError,
)
from .registry import (
    DEFAULT_PROVIDER_REGISTRY,
    ProviderRegistry,
    ProviderStatus,
    UnknownProviderError,
    close_provider,
    create_provider,
    create_security_master_provider,
    get_provider,
)
from .routing import ProviderRouting, build_provider_routing

__all__ = [
    "AkshareSecurityProvider",
    "DailyBarProvider",
    "DEFAULT_PROVIDER_REGISTRY",
    "DividendProvider",
    "EltdxProvider",
    "MarketDataProviderError",
    "ProviderConfigurationError",
    "ProviderRegistry",
    "ProviderRouting",
    "ProviderStatus",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderUnsupportedError",
    "QuoteProvider",
    "SecurityMasterFallbackProvider",
    "SecurityMasterProvider",
    "SymbolProvider",
    "UnknownProviderError",
    "build_provider_routing",
    "close_provider",
    "create_provider",
    "create_security_master_provider",
    "get_provider",
]
