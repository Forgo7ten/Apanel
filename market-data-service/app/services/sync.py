"""Provider-to-repository synchronization services."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.domain.market_data import (
    Adjustment,
    DailyBar,
    Dividend,
    InvalidMarketDataError,
    Quote,
    Security,
    normalize_adjustment,
    normalize_symbol,
)
from app.providers.base import MarketDataProvider, SecurityMasterProvider
from app.providers.errors import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.repositories.market_data import (
    DailyBarRepository,
    DividendRepository,
    PersistenceSystemError,
    QuoteRepository,
    SecurityRepository,
)

SyncStatus = Literal["success", "failed"]


@dataclass(frozen=True, slots=True)
class SyncError:
    """Safe, structured error data returned for one symbol."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SyncItemResult:
    """Outcome for one symbol (or ``*`` for a provider-wide failure)."""

    symbol: str
    status: SyncStatus
    fetched: int = 0
    persisted: int = 0
    error: SyncError | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """Deterministic sync result that callers can serialize directly."""

    operation: str
    items: tuple[SyncItemResult, ...]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(item.succeeded for item in self.items)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded

    @property
    def ok(self) -> bool:
        return self.failed == 0


class SecuritySyncService:
    """Synchronize security metadata as one validated, atomic batch."""

    def __init__(
        self,
        *,
        provider: SecurityMasterProvider,
        repository: SecurityRepository,
    ) -> None:
        self._provider = provider
        self._repository = repository

    async def sync(self) -> SyncSummary:
        try:
            raw_records = tuple(await self._provider.get_symbols())
        except Exception as exc:
            return _security_failure(_sync_error(exc))

        # An empty provider result is deliberately represented by an empty
        # summary.  The bootstrap command treats that stable shape as a
        # retryable "no securities" condition, while no repository write is
        # attempted here.
        if not raw_records:
            return SyncSummary(operation="security", items=())

        records: list[Security] = []
        seen: set[str] = set()
        try:
            for raw_record in raw_records:
                record = _require_security(raw_record)
                if record.symbol in seen:
                    raise InvalidMarketDataError(
                        f"provider returned a duplicate security symbol: {record.symbol}"
                    )
                seen.add(record.symbol)
                records.append(record)
        except Exception as exc:
            return _security_failure(_sync_error(exc))

        try:
            # The repository owns one transaction for this complete batch.  Do
            # not retry by symbol: that would turn a source-wide failure into
            # partial database state and make bootstrap success ambiguous.
            await self._repository.upsert_many(tuple(records))
        except Exception as exc:
            return _security_failure(
                SyncError(code="PERSISTENCE_ERROR", message=_safe_message(exc))
            )

        return SyncSummary(
            operation="security",
            items=tuple(
                SyncItemResult(
                    symbol=record.symbol,
                    status="success",
                    fetched=1,
                    persisted=1,
                )
                for record in records
            ),
        )


class DailyBarSyncService:
    """Fetch and persist bars while isolating provider/repository failures."""

    def __init__(self, *, provider: MarketDataProvider, repository: DailyBarRepository) -> None:
        self._provider = provider
        self._repository = repository

    async def sync(
        self,
        *,
        symbols: Iterable[str],
        start: date,
        end: date,
        adjustment: Adjustment | str = Adjustment.QFQ,
    ) -> SyncSummary:
        selected_adjustment = normalize_adjustment(adjustment)
        canonical_symbols: list[str] = []
        results: dict[str, SyncItemResult] = {}
        for raw_symbol in symbols:
            try:
                symbol = normalize_symbol(raw_symbol)
            except Exception as exc:
                symbol = _best_effort_symbol(raw_symbol)
                results.setdefault(
                    symbol,
                    SyncItemResult(symbol=symbol, status="failed", error=_sync_error(exc)),
                )
                continue
            if symbol not in results:
                canonical_symbols.append(symbol)

        pending: dict[str, tuple[DailyBar, ...]] = {}
        for symbol in canonical_symbols:
            try:
                raw_bars = tuple(
                    await self._provider.get_daily_bars(
                        symbol,
                        start,
                        end,
                        selected_adjustment,
                    )
                )
                bars = _validated_bars(symbol, raw_bars, selected_adjustment)
                pending[symbol] = bars
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="success",
                    fetched=len(bars),
                )
            except Exception as exc:
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="failed",
                    error=_sync_error(exc),
                )

        records = tuple(bar for bars in pending.values() for bar in bars)
        if records:
            persisted = await _persist_with_isolation(
                records,
                self._repository.upsert_many,
                key=lambda record: record.symbol,
            )
            for symbol, error in persisted.items():
                previous = results[symbol]
                results[symbol] = (
                    SyncItemResult(
                        symbol=symbol,
                        status="success",
                        fetched=previous.fetched,
                        persisted=previous.fetched,
                    )
                    if error is None
                    else SyncItemResult(
                        symbol=symbol,
                        status="failed",
                        fetched=previous.fetched,
                        error=error,
                    )
                )
        return SyncSummary(operation="daily_bar", items=tuple(results.values()))


class QuoteSyncService:
    """Fetch and persist one latest quote snapshot per requested symbol."""

    def __init__(self, *, provider: MarketDataProvider, repository: QuoteRepository) -> None:
        self._provider = provider
        self._repository = repository

    async def sync(self, *, symbols: Iterable[str]) -> SyncSummary:
        canonical_symbols, results = _canonicalize_symbols(symbols)
        pending: dict[str, Quote] = {}
        for symbol in canonical_symbols:
            try:
                raw_record = await self._provider.get_quote(symbol)
                record = _validated_quote(symbol, raw_record)
                pending[symbol] = record
                results[symbol] = SyncItemResult(symbol=symbol, status="success", fetched=1)
            except Exception as exc:
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="failed",
                    error=_sync_error(exc),
                )

        if pending:
            persisted = await _persist_with_isolation(
                tuple(pending.values()),
                self._repository.upsert_many,
            )
            for symbol, error in persisted.items():
                previous = results[symbol]
                results[symbol] = (
                    SyncItemResult(
                        symbol=symbol,
                        status="success",
                        fetched=previous.fetched,
                        persisted=1,
                    )
                    if error is None
                    else SyncItemResult(
                        symbol=symbol,
                        status="failed",
                        fetched=previous.fetched,
                        error=error,
                    )
                )
        return SyncSummary(operation="quote", items=tuple(results.values()))


class DividendSyncService:
    """Fetch and persist cash-dividend events with per-symbol isolation."""

    def __init__(self, *, provider: MarketDataProvider, repository: DividendRepository) -> None:
        self._provider = provider
        self._repository = repository

    async def sync(self, *, symbols: Iterable[str]) -> SyncSummary:
        canonical_symbols, results = _canonicalize_symbols(symbols)
        pending: dict[str, tuple[Dividend, ...]] = {}
        for symbol in canonical_symbols:
            try:
                raw_records = tuple(await self._provider.get_dividends(symbol))
                records = _validated_dividends(symbol, raw_records)
                pending[symbol] = records
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="success",
                    fetched=len(records),
                )
            except Exception as exc:
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="failed",
                    error=_sync_error(exc),
                )

        records = tuple(record for values in pending.values() for record in values)
        if records:
            persisted = await _persist_with_isolation(
                records,
                self._repository.upsert_many,
            )
            for symbol, error in persisted.items():
                previous = results[symbol]
                results[symbol] = (
                    SyncItemResult(
                        symbol=symbol,
                        status="success",
                        fetched=previous.fetched,
                        persisted=previous.fetched,
                    )
                    if error is None
                    else SyncItemResult(
                        symbol=symbol,
                        status="failed",
                        fetched=previous.fetched,
                        error=error,
                    )
                )
        return SyncSummary(operation="dividend", items=tuple(results.values()))


def _canonicalize_symbols(
    symbols: Iterable[str],
) -> tuple[list[str], dict[str, SyncItemResult]]:
    canonical_symbols: list[str] = []
    results: dict[str, SyncItemResult] = {}
    for raw_symbol in symbols:
        try:
            symbol = normalize_symbol(raw_symbol)
        except Exception as exc:
            symbol = _best_effort_symbol(raw_symbol)
            results.setdefault(
                symbol,
                SyncItemResult(symbol=symbol, status="failed", error=_sync_error(exc)),
            )
            continue
        if symbol not in results:
            canonical_symbols.append(symbol)
    return canonical_symbols, results


def _validated_quote(symbol: str, value: object) -> Quote:
    if not isinstance(value, Quote):
        raise InvalidMarketDataError("provider returned a non-Quote record")
    if value.symbol != symbol:
        raise InvalidMarketDataError(
            f"provider returned quote for {value.symbol}, requested {symbol}"
        )
    return value


def _validated_dividends(
    symbol: str,
    records: Sequence[object],
) -> tuple[Dividend, ...]:
    unique: dict[tuple[str, date], Dividend] = {}
    for value in records:
        if not isinstance(value, Dividend):
            raise InvalidMarketDataError("provider returned a non-Dividend record")
        if value.symbol != symbol:
            raise InvalidMarketDataError(
                f"provider returned dividend for {value.symbol}, requested {symbol}"
            )
        unique[(value.symbol, value.date)] = value
    return tuple(unique.values())


def _require_security(value: object) -> Security:
    if not isinstance(value, Security):
        raise InvalidMarketDataError("provider returned a non-Security record")
    return value


def _validated_bars(
    symbol: str,
    records: Sequence[object],
    adjustment: Adjustment,
) -> tuple[DailyBar, ...]:
    unique: dict[tuple[str, date, Adjustment], DailyBar] = {}
    for value in records:
        if not isinstance(value, DailyBar):
            raise InvalidMarketDataError("provider returned a non-DailyBar record")
        if value.symbol != symbol:
            raise InvalidMarketDataError(
                f"provider returned bar for {value.symbol}, requested {symbol}"
            )
        if value.adjustment is not adjustment:
            raise InvalidMarketDataError("provider returned a different adjustment mode")
        unique[(value.symbol, value.trade_date, value.adjustment)] = value
    return tuple(unique.values())


async def _persist_with_isolation(
    records: Sequence[object],
    upsert_many,
    *,
    key=lambda record: record.symbol,
) -> dict[str, SyncError | None]:
    """Attempt one batch, then retry each symbol batch when it fails."""

    grouped: dict[str, list[object]] = {}
    for record in records:
        grouped.setdefault(key(record), []).append(record)
    try:
        await upsert_many(tuple(records))
        return {symbol: None for symbol in grouped}
    except Exception as exc:
        # A unique/foreign-key violation can be caused by one malformed
        # record and is safe to isolate below.  Connection, timeout, and
        # transaction failures indicate a broken database and must reach the
        # API as a system error instead of being mislabeled per-symbol data.
        if _is_systemic_persistence_error(exc):
            raise
        outcomes: dict[str, SyncError | None] = {}
        for symbol, symbol_records in grouped.items():
            try:
                await upsert_many(tuple(symbol_records))
            except Exception as exc:
                outcomes[symbol] = SyncError(
                    code="PERSISTENCE_ERROR",
                    message=_safe_message(exc),
                )
            else:
                outcomes[symbol] = None
        return outcomes


def _best_effort_symbol(value: object) -> str:
    if isinstance(value, str):
        try:
            return normalize_symbol(value)
        except Exception:
            return value.strip() or "<invalid>"
    if hasattr(value, "symbol"):
        candidate = value.symbol
        if isinstance(candidate, str):
            return candidate
    return "<invalid>"


def _security_failure(error: SyncError) -> SyncSummary:
    """Return one provider-wide failure without claiming any symbol persisted."""

    return SyncSummary(
        operation="security",
        items=(
            SyncItemResult(
                symbol="*",
                status="failed",
                error=error,
            ),
        ),
    )


def _sync_error(exc: Exception) -> SyncError:
    if isinstance(exc, (ProviderTimeoutError, TimeoutError, asyncio.TimeoutError)):
        code = "PROVIDER_TIMEOUT"
    elif isinstance(exc, ProviderConfigurationError):
        code = "PROVIDER_NOT_CONFIGURED"
    elif isinstance(exc, ProviderUnavailableError):
        code = "PROVIDER_UNAVAILABLE"
    elif isinstance(exc, InvalidMarketDataError):
        code = "INVALID_MARKET_DATA"
    else:
        code = "PROVIDER_ERROR"
    return SyncError(code=code, message=_safe_message(exc))


def _safe_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message[:240] if message else exc.__class__.__name__


def _is_systemic_persistence_error(exc: Exception) -> bool:
    """Return whether retrying individual symbols would hide an outage."""

    if isinstance(exc, PersistenceSystemError):
        return True
    # Integrity errors are the one SQLAlchemy failure we can safely retry per
    # symbol; other SQLAlchemy errors include connection/transaction failures.
    return isinstance(exc, SQLAlchemyError) and not isinstance(exc, IntegrityError)
