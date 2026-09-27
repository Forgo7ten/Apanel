"""Actual trading-day validation through the primary market-data provider."""

from __future__ import annotations

from datetime import date
from typing import Any


class CalendarValidationService:
    def __init__(self, *, provider: Any, repository: Any) -> None:
        self._provider = provider
        self._repository = repository

    async def validate(self, trade_date: date) -> dict[str, Any]:
        checker = getattr(self._provider, "is_trading_day", None)
        if not callable(checker):
            raise RuntimeError("provider does not support trading-day validation")
        actual_open = bool(await checker(trade_date))
        return await self._repository.validate(
            trade_date,
            actual_open=actual_open,
            source=str(getattr(self._provider, "name", "provider")),
        )
