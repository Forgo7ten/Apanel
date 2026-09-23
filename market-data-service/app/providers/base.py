"""Provider contract for TDX, AKShare, and future adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any


class MarketDataProvider(ABC):
    """Async provider interface used by future sync services."""

    @abstractmethod
    async def get_symbols(self) -> Sequence[Mapping[str, Any]]:
        """Return normalized security records."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> Mapping[str, Any]:
        """Return a normalized quote for ``symbol``."""

    @abstractmethod
    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: str = "none",
    ) -> Sequence[Mapping[str, Any]]:
        """Return normalized daily bars for a date range."""

    @abstractmethod
    async def get_dividends(self, symbol: str) -> Sequence[Mapping[str, Any]]:
        """Return normalized dividend events for ``symbol``."""
