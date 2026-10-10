"""One per-series transaction publishes Indicator, State and its durable watermarks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators.parameters import merge_indicator_requests
from app.models import DailyBar, Security
from app.services.analysis_control import lock_analysis_stream
from app.services.analysis_demand import collect_indicator_requests
from app.services.indicator_service import IndicatorService, normalize_symbol
from app.services.state_service import StateService

ANALYSIS_VERSION = "indicator-state-events-v3"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    indicators: int
    states: int
    through: date
    source_revision: int
    rebuilt: bool


class AnalysisService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def materialize(
        self,
        symbol: str,
        *,
        adjustment: str = "qfq",
        end: date | None = None,
        requests: Any = None,
        force_history: bool = False,
    ) -> AnalysisResult:
        try:
            symbol = normalize_symbol(symbol)
            security = (
                await self.session.execute(select(Security).where(Security.symbol == symbol))
            ).scalar_one_or_none()
            if security is None:
                raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
            stream = await lock_analysis_stream(self.session, security.id, adjustment)
            # Read demand after acquiring the publication lock. Config writers
            # advance the same stream row in their own config transaction.
            demanded = (await collect_indicator_requests(self.session, (symbol,))).get(symbol, ())
            effective = merge_indicator_requests([*(requests or ()), *demanded])
            key = hashlib.sha256(
                (
                    ANALYSIS_VERSION + "|" + "|".join(sorted(request.key for request in effective))
                ).encode()
            ).hexdigest()
            query = select(func.min(DailyBar.trade_date), func.max(DailyBar.trade_date)).where(
                DailyBar.security_id == security.id,
                DailyBar.adjust_type == adjustment,
            )
            if end is not None:
                query = query.where(DailyBar.trade_date <= end)
            first, latest = (await self.session.execute(query)).one()
            if latest is None:
                raise ApiError("INDICATOR_DATA_NOT_FOUND", "No completed bars are available.", 404)
            if stream.state_through is not None and latest < stream.state_through:
                raise ApiError(
                    "STALE_ANALYSIS_TARGET",
                    "Historical tasks cannot replace current analysis.",
                    409,
                )
            changed_demand = stream.demand_key != key
            rebuild = force_history or changed_demand or stream.published_revision < 0
            start = None if rebuild else stream.dirty_from
            if not rebuild and start is None:
                through = min(stream.indicator_through or first, stream.state_through or first)
                if latest <= through and stream.published_revision == stream.source_revision:
                    stream.published_demand_generation = stream.demand_generation
                    await self.session.commit()
                    return AnalysisResult(0, 0, latest, stream.source_revision, False)
                start = (
                    await self.session.execute(
                        select(func.min(DailyBar.trade_date)).where(
                            DailyBar.security_id == security.id,
                            DailyBar.adjust_type == adjustment,
                            DailyBar.trade_date > through,
                            DailyBar.trade_date <= latest,
                        )
                    )
                ).scalar_one_or_none()
                if start is None:
                    start = first
            # No network I/O occurs under this lock. Both observations and
            # checkpoints become visible together; rollback retains invalidation.
            indicators = await IndicatorService(self.session).rebuild_history(
                symbol,
                adjustment=adjustment,
                requests=effective,
                start=start,
                end=latest,
                commit=False,
                source_revision=stream.source_revision,
                restatement_through=stream.indicator_through or latest,
            )
            states = (
                []
                if not indicators
                else await StateService(self.session).rebuild_history(
                    symbol,
                    adjustment=adjustment,
                    start=start,
                    end=latest,
                    commit=False,
                )
            )
            if not indicators:
                from app.repositories.state_event import StateEventRepository

                await StateEventRepository(self.session).publish(
                    stream, [], start=None, through=latest
                )
            stream.demand_key = key
            stream.published_revision = stream.source_revision
            stream.published_demand_generation = stream.demand_generation
            stream.indicator_through = stream.state_through = latest
            stream.latest_bar_date = max(stream.latest_bar_date or latest, latest)
            if stream.latest_bar_date <= latest:
                stream.dirty_from = None
            stream.updated_at = datetime.now(UTC)
            result = AnalysisResult(
                len(indicators), len(states), latest, stream.source_revision, rebuild
            )
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            raise
