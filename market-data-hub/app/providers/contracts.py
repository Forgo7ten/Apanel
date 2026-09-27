"""Capability-specific provider contracts for the Market Data Hub."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from app.domain.market_data import Adjustment, DailyBar, Dividend, Quote, Security


@runtime_checkable
class SecurityMasterProvider(Protocol):
    """Return one complete, validated security-master batch."""

    async def get_symbols(self) -> Sequence[Security]: ...


@runtime_checkable
class QuoteProvider(Protocol):
    """Return a validated current quote for one canonical symbol."""

    async def get_quote(self, symbol: str) -> Quote: ...


@runtime_checkable
class BulkQuoteProvider(Protocol):
    """Optional provider-native bulk quote capability."""

    async def get_quotes(self, symbols: Sequence[str]) -> Sequence[Quote]: ...


@runtime_checkable
class DailyBarProvider(Protocol):
    """Return validated daily bars under an explicit adjustment mode."""

    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: Adjustment | str = Adjustment.QFQ,
    ) -> Sequence[DailyBar]: ...


@runtime_checkable
class BatchDailyBarProvider(Protocol):
    """Optional provider-native code-list/count daily-bar capability."""

    async def get_daily_bars_batch(
        self,
        symbols: Sequence[str],
        *,
        adjustment: Adjustment | str = Adjustment.QFQ,
        count: int,
    ) -> dict[str, Sequence[DailyBar]]: ...


@runtime_checkable
class AdjustmentRevisionProvider(Protocol):
    async def get_adjustment_revision(self, symbol: str) -> str: ...


@runtime_checkable
class DividendProvider(Protocol):
    """Return validated per-share cash-dividend events."""

    async def get_dividends(self, symbol: str) -> Sequence[Dividend]: ...


SymbolProvider = SecurityMasterProvider


__all__ = [
    "AdjustmentRevisionProvider",
    "BatchDailyBarProvider",
    "BulkQuoteProvider",
    "DailyBarProvider",
    "DividendProvider",
    "QuoteProvider",
    "SecurityMasterProvider",
    "SymbolProvider",
]
