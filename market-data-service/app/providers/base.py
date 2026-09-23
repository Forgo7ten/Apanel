"""Typed provider contract for TDX, AKShare, and future adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date

from app.domain.market_data import Adjustment, DailyBar, Dividend, Quote, Security


class MarketDataProvider(ABC):
    """Async provider interface used by sync services.

    Implementations must return validated domain records. Raw provider
    mappings belong inside the adapter and never cross this boundary.
    """

    @abstractmethod
    async def get_symbols(self) -> Sequence[Security]:
        """Return validated security metadata."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Return a validated quote for ``symbol``."""

    @abstractmethod
    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: Adjustment | str = Adjustment.NONE,
    ) -> Sequence[DailyBar]:
        """Return validated daily bars for a date range."""

    @abstractmethod
    async def get_dividends(self, symbol: str) -> Sequence[Dividend]:
        """Return validated cash dividend events for ``symbol``."""
