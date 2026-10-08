"""Minimum-history readiness checks for scheduled analysis pipelines."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select

from app.indicators.parameters import IndicatorRequest, required_history_bars
from app.models import DailyBar, Security, TradingCalendar
from app.services.data_bootstrap_service import SecurityDataBootstrapService


@dataclass(frozen=True, slots=True)
class HistoryReadinessResult:
    checked_symbols: tuple[str, ...]
    bootstrapped_symbols: tuple[str, ...]
    required_bars: dict[str, int]


class DataReadinessService:
    """Ensure monitored securities have enough bars for their indicator demand.

    This service deliberately does not evaluate alerts.  It only invokes the
    shared bootstrap path that synchronizes market facts and materializes
    indicator/state observations.  That makes it safe to call before the
    normal EOD pipeline after an API-side bootstrap enqueue was lost.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        bootstrap_service: SecurityDataBootstrapService,
        minimum_bars: int = 400,
    ) -> None:
        self.session_factory = session_factory
        self.bootstrap_service = bootstrap_service
        self.minimum_bars = max(1, int(minimum_bars))

    async def ensure_minimum_history(
        self,
        symbols: Sequence[str],
        *,
        adjustments: Sequence[str],
        requests_by_symbol: Mapping[str, Sequence[IndicatorRequest] | None],
        completed_through: date | None = None,
    ) -> HistoryReadinessResult:
        normalized_symbols = tuple(
            dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())
        )
        normalized_adjustments = tuple(
            dict.fromkeys(str(item).strip().lower() for item in adjustments if str(item).strip())
        )
        if not normalized_symbols or not normalized_adjustments:
            return HistoryReadinessResult(normalized_symbols, (), {})

        required = {
            symbol: max(
                self.minimum_bars,
                required_history_bars(requests_by_symbol.get(symbol)),
            )
            for symbol in normalized_symbols
        }
        histories = await self._bar_dates(
            normalized_symbols,
            normalized_adjustments,
            completed_through=completed_through,
        )
        expected_open_dates: tuple[date, ...] = ()
        if completed_through is not None:
            recent_starts = [
                dates[-required[symbol]]
                for (symbol, _adjustment), dates in histories.items()
                if len(dates) >= required[symbol]
            ]
            if recent_starts:
                expected_open_dates = await self._open_dates(
                    min(recent_starts), completed_through
                )
        bootstrapped: list[str] = []
        for symbol in normalized_symbols:
            missing = tuple(
                adjustment
                for adjustment in normalized_adjustments
                if not _history_is_ready(
                    histories.get((symbol, adjustment), ()),
                    required=required[symbol],
                    expected_open_dates=expected_open_dates,
                )
            )
            if not missing:
                continue
            await self.bootstrap_service.ensure_ready(
                symbol,
                adjustments=missing,
                requests=tuple(requests_by_symbol.get(symbol) or ()),
                completed_through=completed_through,
            )
            bootstrapped.append(symbol)
        return HistoryReadinessResult(
            checked_symbols=normalized_symbols,
            bootstrapped_symbols=tuple(bootstrapped),
            required_bars=required,
        )

    async def _bar_dates(
        self,
        symbols: tuple[str, ...],
        adjustments: tuple[str, ...],
        *,
        completed_through: date | None,
    ) -> dict[tuple[str, str], tuple[date, ...]]:
        async with self.session_factory() as session:
            statement = (
                select(Security.symbol, DailyBar.adjust_type, DailyBar.trade_date)
                .join(DailyBar, DailyBar.security_id == Security.id)
                .where(
                    Security.symbol.in_(symbols),
                    DailyBar.adjust_type.in_(adjustments),
                )
                .order_by(
                    Security.symbol.asc(),
                    DailyBar.adjust_type.asc(),
                    DailyBar.trade_date.asc(),
                )
            )
            if completed_through is not None:
                statement = statement.where(DailyBar.trade_date <= completed_through)
            rows = (await session.execute(statement)).all()
        grouped: dict[tuple[str, str], list[date]] = {}
        for symbol, adjustment, trade_date in rows:
            grouped.setdefault(
                (str(symbol).upper(), str(adjustment).lower()), []
            ).append(trade_date)
        return {key: tuple(values) for key, values in grouped.items()}

    async def _open_dates(self, start: date, end: date) -> tuple[date, ...]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(TradingCalendar.trade_date)
                    .where(
                        TradingCalendar.market == "CN",
                        TradingCalendar.trade_date >= start,
                        TradingCalendar.trade_date <= end,
                        TradingCalendar.status == "OPEN",
                    )
                    .order_by(TradingCalendar.trade_date.asc())
                )
            ).scalars()
            return tuple(rows)


def _history_is_ready(
    dates: Sequence[date],
    *,
    required: int,
    expected_open_dates: Sequence[date],
) -> bool:
    if len(dates) < required:
        return False
    if not expected_open_dates:
        return True
    recent = tuple(dates[-required:])
    recent_set = set(recent)
    expected_recent = tuple(day for day in expected_open_dates if day >= recent[0])
    if not expected_recent:
        return True
    return expected_recent[-1] in recent_set and all(day in recent_set for day in expected_recent)


__all__ = ["DataReadinessService", "HistoryReadinessResult"]
