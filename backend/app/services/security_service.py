"""Application service for public securities and market data."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.repositories.security import SecurityRepository
from app.services.dividend_service import DividendYieldResult, DividendYieldService
from app.services.indicator_service import normalize_symbol


class SecurityService:
    """Read-only market-data operations with stable domain errors."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = SecurityRepository(session)
        self.dividend_yield_service = DividendYieldService(self.repository)

    async def search(self, query: str, *, limit: int = 50):
        normalized = query.strip()
        if len(normalized) > 128:
            raise ApiError("INVALID_QUERY", "Search query is too long.", 400)
        if not normalized:
            return []
        return await self.repository.search(normalized, limit=max(1, min(limit, 100)))

    async def get(self, symbol: str):
        security = await self.repository.get_by_symbol(normalize_symbol(symbol))
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        return security

    async def quote(self, symbol: str):
        security = await self.get(symbol)
        quote = await self.repository.latest_quote(security.id)
        if quote is None:
            raise ApiError("QUOTE_NOT_FOUND", "No quote is available.", 404)
        return quote

    async def dividend_yield(self, symbol: str) -> DividendYieldResult:
        """Return explainable TTM cash-dividend yield for a public symbol."""

        security = await self.get(symbol)
        return await self.dividend_yield_service.calculate_for_security(security.id)

    async def dividend_yield_for_security(self, security_id: int) -> DividendYieldResult:
        """Calculate TTM yield for a known security id for watch aggregation."""

        return await self.dividend_yield_service.calculate_for_security(security_id)

    async def daily_bars(
        self,
        symbol: str,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str | None = None,
    ):
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ApiError("INVALID_DATE_RANGE", "Start date must not be after end date.", 400)
        if adjust_type is not None and adjust_type not in {"qfq", "none"}:
            raise ApiError("INVALID_ADJUSTMENT", "Adjustment must be qfq or none.", 400)
        security = await self.get(symbol)
        return await self.repository.daily_bars(
            security.id,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
        )


__all__ = ["SecurityService"]
