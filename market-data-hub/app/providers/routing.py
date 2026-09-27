"""Capability routing inside the Market Data Hub.

The backend never sees this layer.  Routing is intentionally capability-based
so future providers can implement only the market-data operations they own.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import DailyBarProvider, DividendProvider, QuoteProvider, SecurityMasterProvider


@dataclass(frozen=True, slots=True)
class ProviderRouting:
    """Resolved provider for each v1 market-data capability."""

    security_master: SecurityMasterProvider
    quote: QuoteProvider
    daily_bar: DailyBarProvider
    dividend: DividendProvider


def build_provider_routing(
    *,
    primary: object,
    security_master: SecurityMasterProvider,
) -> ProviderRouting:
    """Validate the configured v1 provider graph at application startup."""

    missing: list[str] = []
    if not isinstance(primary, QuoteProvider):
        missing.append("quote")
    if not isinstance(primary, DailyBarProvider):
        missing.append("daily_bar")
    if not isinstance(primary, DividendProvider):
        missing.append("dividend")
    if not isinstance(primary, SecurityMasterProvider):
        missing.append("security_master")
    if missing:
        raise TypeError(f"primary provider is missing capabilities: {', '.join(missing)}")
    if not isinstance(security_master, SecurityMasterProvider):
        raise TypeError("security master provider does not implement SecurityMasterProvider")
    return ProviderRouting(
        security_master=security_master,
        quote=primary,
        daily_bar=primary,
        dividend=primary,
    )


__all__ = ["ProviderRouting", "build_provider_routing"]
