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
from app.providers.contracts import (
    DailyBarProvider,
    DividendProvider,
    QuoteProvider,
    SecurityMasterProvider,
)
from app.providers.errors import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.repositories.market_data import (
    AdjustmentStateRepository,
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
    history_rebased: bool = False
    changed_from: date | None = None

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
    """Fetch and persist bars using native batching plus qfq revision tracking."""

    def __init__(
        self,
        *,
        provider: DailyBarProvider,
        repository: DailyBarRepository,
        adjustment_repository: AdjustmentStateRepository | None = None,
        batch_size: int = 50,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._adjustment_repository = adjustment_repository
        self._batch_size = max(1, int(batch_size))

    async def sync(
        self,
        *,
        symbols: Iterable[str],
        start: date | None = None,
        end: date | None = None,
        adjustment: Adjustment | str = Adjustment.QFQ,
        lookback_bars: int | None = None,
    ) -> SyncSummary:
        selected_adjustment = normalize_adjustment(adjustment)
        if lookback_bars is None:
            if start is None or end is None or start > end:
                raise InvalidMarketDataError("start/end are required and must form a valid range")
            count = max(8, (end - start).days * 2 + 8)
        else:
            if start is not None or end is not None or lookback_bars <= 0:
                raise InvalidMarketDataError("lookback_bars is exclusive with start/end")
            count = int(lookback_bars)
        canonical_symbols, results = _canonicalize_symbols(symbols)
        pending: dict[str, tuple[DailyBar, ...]] = {}
        revision_by_symbol: dict[str, str] = {}
        rebase_from: dict[str, date] = {}
        batch_method = getattr(self._provider, "get_daily_bars_batch", None)
        revision_method = getattr(self._provider, "get_adjustment_revision", None)

        normal_symbols = list(canonical_symbols)
        if (
            selected_adjustment is Adjustment.QFQ
            and self._adjustment_repository is not None
            and callable(revision_method)
            and callable(batch_method)
        ):
            normal_symbols = []
            for symbol in canonical_symbols:
                try:
                    revision = await revision_method(symbol)
                    revision_by_symbol[symbol] = revision
                    state = await self._adjustment_repository.get(symbol)
                    if state is not None and state.get("revision_hash") != revision:
                        existing = await self._repository.list_by_symbol(
                            symbol, adjustment=selected_adjustment
                        )
                        full_count = max(count, len(existing) + 32, 400)
                        by_symbol = await batch_method(
                            (symbol,), adjustment=selected_adjustment, count=full_count
                        )
                        bars = _validated_bars(
                            symbol,
                            tuple(by_symbol.get(symbol, ())),
                            selected_adjustment,
                        )
                        pending[symbol] = bars
                        changed_from = min(
                            (item.trade_date for item in existing), default=None
                        ) or min((item.trade_date for item in bars), default=None)
                        if changed_from is not None:
                            rebase_from[symbol] = changed_from
                        results[symbol] = SyncItemResult(
                            symbol=symbol,
                            status="success",
                            fetched=len(bars),
                            history_rebased=True,
                            changed_from=changed_from,
                        )
                        continue
                    normal_symbols.append(symbol)
                except Exception as exc:
                    results[symbol] = SyncItemResult(
                        symbol=symbol, status="failed", error=_sync_error(exc)
                    )

        if callable(batch_method):
            for offset in range(0, len(normal_symbols), self._batch_size):
                chunk = normal_symbols[offset : offset + self._batch_size]
                try:
                    by_symbol = await batch_method(
                        chunk,
                        adjustment=selected_adjustment,
                        count=count,
                    )
                except Exception as exc:
                    for symbol in chunk:
                        results[symbol] = SyncItemResult(
                            symbol=symbol, status="failed", error=_sync_error(exc)
                        )
                    continue
                for symbol in chunk:
                    try:
                        raw = tuple(by_symbol.get(symbol, ()))
                        if start is not None and end is not None:
                            raw = tuple(item for item in raw if start <= item.trade_date <= end)
                        bars = _validated_bars(symbol, raw, selected_adjustment)
                        pending[symbol] = bars
                        results[symbol] = SyncItemResult(
                            symbol=symbol, status="success", fetched=len(bars)
                        )
                    except Exception as exc:
                        results[symbol] = SyncItemResult(
                            symbol=symbol, status="failed", error=_sync_error(exc)
                        )
        else:
            if start is None or end is None:
                raise InvalidMarketDataError(
                    "provider does not support count-based history bootstrap"
                )
            for symbol in normal_symbols:
                try:
                    bars = _validated_bars(
                        symbol,
                        tuple(
                            await self._provider.get_daily_bars(
                                symbol, start, end, selected_adjustment
                            )
                        ),
                        selected_adjustment,
                    )
                    pending[symbol] = bars
                    results[symbol] = SyncItemResult(
                        symbol=symbol, status="success", fetched=len(bars)
                    )
                except Exception as exc:
                    results[symbol] = SyncItemResult(
                        symbol=symbol, status="failed", error=_sync_error(exc)
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
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="success" if error is None else "failed",
                    fetched=previous.fetched,
                    persisted=previous.fetched if error is None else 0,
                    error=error,
                    history_rebased=previous.history_rebased,
                    changed_from=previous.changed_from,
                )

        if selected_adjustment is Adjustment.QFQ and self._adjustment_repository is not None:
            for symbol in canonical_symbols:
                item = results.get(symbol)
                if item is None or not item.succeeded:
                    continue
                revision = revision_by_symbol.get(symbol)
                if revision is None and callable(revision_method):
                    try:
                        revision = await revision_method(symbol)
                    except Exception:
                        continue
                if not revision:
                    continue
                try:
                    coverage = await self._repository.list_by_symbol(
                        symbol, adjustment=selected_adjustment
                    )
                    await self._adjustment_repository.upsert(
                        symbol,
                        provider_name=str(getattr(self._provider, "name", "provider")),
                        revision_hash=revision,
                        coverage_start=min((bar.trade_date for bar in coverage), default=None),
                        coverage_end=max((bar.trade_date for bar in coverage), default=None),
                        rebased=symbol in rebase_from,
                    )
                except Exception as exc:
                    previous = results[symbol]
                    results[symbol] = SyncItemResult(
                        symbol=symbol,
                        status="failed",
                        fetched=previous.fetched,
                        persisted=previous.persisted,
                        error=SyncError(code="PERSISTENCE_ERROR", message=_safe_message(exc)),
                        history_rebased=previous.history_rebased,
                        changed_from=previous.changed_from,
                    )
        return SyncSummary(operation="daily_bar", items=tuple(results.values()))


class QuoteSyncService:
    """Fetch and persist latest quote snapshots with provider-native batching."""

    def __init__(
        self, *, provider: QuoteProvider, repository: QuoteRepository, batch_size: int = 50
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._batch_size = max(1, int(batch_size))

    async def sync(self, *, symbols: Iterable[str]) -> SyncSummary:
        canonical_symbols, results = _canonicalize_symbols(symbols)
        pending: dict[str, Quote] = {}
        batch_method = getattr(self._provider, "get_quotes", None)
        if callable(batch_method):
            for offset in range(0, len(canonical_symbols), self._batch_size):
                chunk = canonical_symbols[offset : offset + self._batch_size]
                try:
                    records = tuple(await batch_method(chunk))
                    by_symbol = {
                        record.symbol: _validated_quote(record.symbol, record) for record in records
                    }
                    for symbol in chunk:
                        if symbol not in by_symbol:
                            raise InvalidMarketDataError(f"provider returned no quote for {symbol}")
                        pending[symbol] = by_symbol[symbol]
                        results[symbol] = SyncItemResult(symbol=symbol, status="success", fetched=1)
                except Exception as exc:
                    for symbol in chunk:
                        if symbol not in pending:
                            results[symbol] = SyncItemResult(
                                symbol=symbol, status="failed", error=_sync_error(exc)
                            )
        else:
            for symbol in canonical_symbols:
                try:
                    record = _validated_quote(symbol, await self._provider.get_quote(symbol))
                    pending[symbol] = record
                    results[symbol] = SyncItemResult(symbol=symbol, status="success", fetched=1)
                except Exception as exc:
                    results[symbol] = SyncItemResult(
                        symbol=symbol, status="failed", error=_sync_error(exc)
                    )
        if pending:
            persisted = await _persist_with_isolation(
                tuple(pending.values()), self._repository.upsert_many
            )
            for symbol, error in persisted.items():
                previous = results[symbol]
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="success" if error is None else "failed",
                    fetched=previous.fetched,
                    persisted=1 if error is None else 0,
                    error=error,
                )
        return SyncSummary(operation="quote", items=tuple(results.values()))


class DividendSyncService:
    """Fetch dividends with bounded concurrency and persist per-symbol results."""

    def __init__(
        self, *, provider: DividendProvider, repository: DividendRepository, concurrency: int = 4
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._concurrency = max(1, int(concurrency))

    async def sync(self, *, symbols: Iterable[str]) -> SyncSummary:
        canonical_symbols, results = _canonicalize_symbols(symbols)
        semaphore = asyncio.Semaphore(self._concurrency)

        async def fetch(symbol: str):
            async with semaphore:
                try:
                    records = _validated_dividends(
                        symbol, tuple(await self._provider.get_dividends(symbol))
                    )
                    return symbol, records, None
                except Exception as exc:
                    return symbol, (), _sync_error(exc)

        pending: dict[str, tuple[Dividend, ...]] = {}
        for symbol, records, error in await asyncio.gather(
            *(fetch(symbol) for symbol in canonical_symbols)
        ):
            if error is not None:
                results[symbol] = SyncItemResult(symbol=symbol, status="failed", error=error)
            else:
                pending[symbol] = records
                results[symbol] = SyncItemResult(
                    symbol=symbol, status="success", fetched=len(records)
                )
        records = tuple(record for values in pending.values() for record in values)
        if records:
            persisted = await _persist_with_isolation(records, self._repository.upsert_many)
            for symbol, error in persisted.items():
                previous = results[symbol]
                results[symbol] = SyncItemResult(
                    symbol=symbol,
                    status="success" if error is None else "failed",
                    fetched=previous.fetched,
                    persisted=previous.fetched if error is None else 0,
                    error=error,
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
