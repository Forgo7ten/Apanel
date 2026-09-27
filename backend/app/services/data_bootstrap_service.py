"""Idempotent shared market-data and analysis bootstrap for monitored securities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.clients.market_data_hub import MarketDataHubClientProtocol
from app.indicators.parameters import IndicatorRequest, required_history_bars
from app.services.indicator_service import IndicatorService
from app.services.state_service import StateService


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
    ) -> BootstrapResult:
        bars = max(self.minimum_bars, required_history_bars(requests))
        indicator_count = 0
        state_count = 0
        for adjustment in tuple(dict.fromkeys(adjustments)):
            result = await self.market_data_client.sync_daily(
                symbols=(symbol,),
                lookback_bars=bars,
                adjustment=adjustment,
            )
            if not result.ok:
                raise RuntimeError("market data bootstrap did not complete")
            async with self.session_factory() as session:
                indicator_count += len(
                    await IndicatorService(session).rebuild_history(
                        symbol,
                        adjustment=adjustment,
                        requests=requests,
                    )
                )
            async with self.session_factory() as session:
                state_count += len(
                    await StateService(session).rebuild_history(
                        symbol,
                        adjustment=adjustment,
                    )
                )
        dividend_result = await self.market_data_client.sync_dividends(symbols=(symbol,))
        if not dividend_result.ok:
            raise RuntimeError("dividend bootstrap did not complete")
        return BootstrapResult(
            symbol=symbol,
            adjustments=adjustments,
            dividend_synced=dividend_result.ok,
            indicators_materialized=indicator_count,
            states_materialized=state_count,
        )
