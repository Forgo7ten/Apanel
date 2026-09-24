"""TTM cash-dividend yield calculation and price-source policy."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import ApiError
from app.repositories.security import DividendEventRecord, SecurityRepository


@dataclass(frozen=True, slots=True)
class DividendYieldResult:
    """Explainable TTM yield projection returned by the application layer."""

    dividend_total: Decimal
    price: Decimal
    dividend_yield: Decimal
    as_of: datetime
    price_source: str
    window_start: date
    window_end: date
    dividend_event_count: int


class DividendYieldCalculationError(ValueError):
    """A deterministic input/data error from the pure yield calculation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DividendYieldService:
    """Calculate TTM yield from repository data without HTTP concerns."""

    def __init__(self, repository: SecurityRepository) -> None:
        self.repository = repository

    async def calculate_for_security(self, security_id: int) -> DividendYieldResult:
        """Calculate yield using the newest quote, then a daily-close fallback."""

        quote = await self.repository.latest_quote(security_id)
        if quote is not None:
            price = quote.price
            as_of = _utc_timestamp(quote.timestamp)
            price_source = "QUOTE"
        else:
            daily_bar = await self.repository.latest_daily_bar(security_id)
            if daily_bar is None:
                raise ApiError(
                    "PRICE_NOT_FOUND",
                    "No quote or daily close is available for this security.",
                    404,
                )
            price = daily_bar.close
            as_of = datetime.combine(daily_bar.trade_date, time.min, tzinfo=UTC)
            price_source = "DAILY_BAR_CLOSE"

        window_start, window_end = ttm_window(as_of)
        events = await self.repository.dividend_events(
            security_id,
            start_date=window_start,
            end_date=window_end,
        )
        try:
            return calculate_ttm_dividend_yield(
                events,
                price=price,
                as_of=as_of,
                price_source=price_source,
            )
        except DividendYieldCalculationError as exc:
            raise ApiError(exc.code, exc.message, 422) from exc


def calculate_ttm_dividend_yield(
    events: Iterable[DividendEventRecord],
    *,
    price: Any,
    as_of: date | datetime,
    price_source: str,
) -> DividendYieldResult:
    """Calculate ``sum(cash_amount) / price`` over an inclusive 12-month window.

    The lower and upper window boundaries are both inclusive.  The boundary
    uses calendar-month arithmetic (rather than 365 elapsed days), so a quote
    on February 29 correctly maps to February 28 of the prior year.
    """

    normalized_price = _to_decimal(price, field="price")
    if normalized_price <= 0:
        raise DividendYieldCalculationError(
            "INVALID_PRICE", "Price must be greater than zero to calculate yield."
        )

    normalized_as_of = _as_of_datetime(as_of)
    window_start, window_end = ttm_window(normalized_as_of)
    total = Decimal("0")
    event_count = 0
    for event in events:
        event_date = _event_date(event)
        if event_date < window_start or event_date > window_end:
            continue
        amount = _to_decimal(_event_value(event, "cash_amount"), field="cash_amount")
        if amount < 0:
            raise DividendYieldCalculationError(
                "INVALID_DIVIDEND_DATA",
                "Cash dividend amounts must not be negative.",
            )
        total += amount
        event_count += 1

    try:
        dividend_yield = total / normalized_price
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise DividendYieldCalculationError(
            "INVALID_PRICE", "Price must be greater than zero to calculate yield."
        ) from exc

    return DividendYieldResult(
        dividend_total=total,
        price=normalized_price,
        dividend_yield=dividend_yield,
        as_of=normalized_as_of,
        price_source=price_source,
        window_start=window_start,
        window_end=window_end,
        dividend_event_count=event_count,
    )


def ttm_window(as_of: date | datetime) -> tuple[date, date]:
    """Return the inclusive calendar twelve-month window ending at ``as_of``."""

    end = as_of.date() if isinstance(as_of, datetime) else as_of
    if not isinstance(end, date):
        raise DividendYieldCalculationError("INVALID_AS_OF", "as_of must be a date or datetime.")
    prior_year = end.year - 1
    prior_month_day = min(end.day, calendar.monthrange(prior_year, end.month)[1])
    return date(prior_year, end.month, prior_month_day), end


def _as_of_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return _utc_timestamp(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    raise DividendYieldCalculationError("INVALID_AS_OF", "as_of must be a date or datetime.")


def _utc_timestamp(value: datetime) -> datetime:
    """Make database timestamps explicit when a backend returns naive UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _to_decimal(value: Any, *, field: str) -> Decimal:
    if value is None:
        raise DividendYieldCalculationError(
            "INVALID_PRICE" if field == "price" else "INVALID_DIVIDEND_DATA",
            f"{field} is required for TTM dividend yield.",
        )
    try:
        normalized = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DividendYieldCalculationError(
            "INVALID_PRICE" if field == "price" else "INVALID_DIVIDEND_DATA",
            f"{field} must be a valid decimal number.",
        ) from exc
    if not normalized.is_finite():
        raise DividendYieldCalculationError(
            "INVALID_PRICE" if field == "price" else "INVALID_DIVIDEND_DATA",
            f"{field} must be finite.",
        )
    return normalized


def _event_value(event: Any, field: str) -> Any:
    if isinstance(event, dict):
        return event[field]
    return getattr(event, field)


def _event_date(event: Any) -> date:
    value = _event_value(event, "date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise DividendYieldCalculationError(
            "INVALID_DIVIDEND_DATA", "Dividend event date must be a valid date."
        ) from exc


__all__ = [
    "DividendYieldCalculationError",
    "DividendYieldResult",
    "DividendYieldService",
    "calculate_ttm_dividend_yield",
    "ttm_window",
]
