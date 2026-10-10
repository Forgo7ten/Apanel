"""Idempotent shared market-data and analysis bootstrap for monitored securities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select

from app.clients.market_data_hub import MarketDataHubClientProtocol
from app.core.errors import ApiError
from app.indicators.parameters import IndicatorRequest, required_history_bars
from app.models import AnalysisStream, DailyBar, Security
from app.services.analysis_service import AnalysisService


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    symbol: str
    adjustments: tuple[str, ...]
    dividend_synced: bool
    indicators_materialized: int
    states_materialized: int


class SecurityDataBootstrapService:
    def __init__(
        self,
        *,
        session_factory: Any,
        market_data_client: MarketDataHubClientProtocol,
        minimum_bars: int = 400,
    ) -> None:
        self.session_factory = session_factory
        self.market_data_client = market_data_client
        self.minimum_bars = max(1, int(minimum_bars))

    async def ensure_ready(
        self,
        symbol: str,
        *,
        adjustments: tuple[str, ...] = ("qfq", "none"),
        requests: tuple[IndicatorRequest, ...] | None = None,
        completed_through: date | None = None,
    ) -> BootstrapResult:
        bars = max(self.minimum_bars, required_history_bars(requests))
        indicator_count = 0
        state_count = 0
        async with self.session_factory() as session:
            published = (
                await session.execute(
                    select(func.max(AnalysisStream.state_through))
                    .join(Security, Security.id == AnalysisStream.security_id)
                    .where(Security.symbol == symbol)
                )
            ).scalar_one_or_none()
        # Existing published data is proof of completion, unlike a wall-clock guess.
        if published is not None and completed_through is not None:
            completed_through = max(published, completed_through)
        for adjustment in tuple(dict.fromkeys(adjustments)):
            async with self.session_factory() as session:
                row = (
                    await session.execute(
                        select(AnalysisStream)
                        .join(Security, Security.id == AnalysisStream.security_id)
                        .where(Security.symbol == symbol, AnalysisStream.adjust_type == adjustment)
                    )
                ).scalar_one_or_none()
                query = (
                    select(func.count(DailyBar.id))
                    .join(Security)
                    .where(Security.symbol == symbol, DailyBar.adjust_type == adjustment)
                )
                if completed_through is not None:
                    query = query.where(DailyBar.trade_date <= completed_through)
                count = (await session.execute(query)).scalar_one()
                needs_history = count < bars and (row is None or row.history_depth < bars)
            if needs_history:
                result = await self.market_data_client.sync_daily(
                    symbols=(symbol,),
                    lookback_bars=bars,
                    end=completed_through,
                    adjustment=adjustment,
                )
                if not result.ok:
                    raise ApiError(
                        "BOOTSTRAP_SYNC_FAILED", "Market data bootstrap did not complete.", 503
                    )
            async with self.session_factory() as session:
                result = await AnalysisService(session).materialize(
                    symbol,
                    adjustment=adjustment,
                    requests=requests,
                    end=completed_through,
                )
                indicator_count += result.indicators
                state_count += result.states
        dividend_result = await self.market_data_client.sync_dividends(symbols=(symbol,))
        if not dividend_result.ok:
            raise ApiError("BOOTSTRAP_SYNC_FAILED", "Dividend bootstrap did not complete.", 503)
        async with self.session_factory() as session:
            security = (
                await session.execute(select(Security).where(Security.symbol == symbol))
            ).scalar_one_or_none()
            if security is not None:
                security.bootstrap_completed_at = datetime.now(UTC)
            await session.commit()
        return BootstrapResult(
            symbol=symbol,
            adjustments=adjustments,
            dividend_synced=dividend_result.ok,
            indicators_materialized=indicator_count,
            states_materialized=state_count,
        )
