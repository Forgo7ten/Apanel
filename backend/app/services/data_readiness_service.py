"""Minimum-history readiness checks for scheduled analysis pipelines."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.indicators.parameters import IndicatorRequest, required_history_bars
from app.models import DailyBar, Security
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
        counts = await self._bar_counts(normalized_symbols, normalized_adjustments)
        bootstrapped: list[str] = []
        for symbol in normalized_symbols:
            missing = tuple(
                adjustment
                for adjustment in normalized_adjustments
                if counts.get((symbol, adjustment), 0) < required[symbol]
            )
            if not missing:
                continue
            await self.bootstrap_service.ensure_ready(
                symbol,
                adjustments=missing,
                requests=tuple(requests_by_symbol.get(symbol) or ()),
            )
            bootstrapped.append(symbol)
        return HistoryReadinessResult(
            checked_symbols=normalized_symbols,
            bootstrapped_symbols=tuple(bootstrapped),
            required_bars=required,
        )

    async def _bar_counts(
        self,
        symbols: tuple[str, ...],
        adjustments: tuple[str, ...],
    ) -> dict[tuple[str, str], int]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(Security.symbol, DailyBar.adjust_type, func.count(DailyBar.id))
                    .join(DailyBar, DailyBar.security_id == Security.id)
                    .where(
                        Security.symbol.in_(symbols),
                        DailyBar.adjust_type.in_(adjustments),
                    )
                    .group_by(Security.symbol, DailyBar.adjust_type)
                )
            ).all()
        return {
            (str(symbol).upper(), str(adjustment).lower()): int(count)
            for symbol, adjustment, count in rows
        }


__all__ = ["DataReadinessService", "HistoryReadinessResult"]
