"""Shared bounds for public persisted-history reads."""

from __future__ import annotations

from datetime import date, timedelta

from app.core.errors import ApiError

DEFAULT_HISTORY_WINDOW_DAYS = 90
MAX_HISTORY_WINDOW_DAYS = 366
MAX_STATE_HISTORY_ROWS = 20_000


def resolve_history_window(
    *,
    start: date | None,
    end: date | None,
    latest_date: date,
) -> tuple[date, date]:
    """Return an inclusive bounded history range anchored to persisted data."""

    if start is not None and end is not None and start > end:
        raise ApiError("INVALID_DATE_RANGE", "Start date must not be after end date.", 400)

    if start is None and end is None:
        resolved_end = latest_date
        resolved_start = resolved_end - timedelta(days=DEFAULT_HISTORY_WINDOW_DAYS - 1)
    elif start is None:
        resolved_end = end
        assert resolved_end is not None
        resolved_start = resolved_end - timedelta(days=DEFAULT_HISTORY_WINDOW_DAYS - 1)
    elif end is None:
        resolved_start = start
        resolved_end = min(
            latest_date,
            resolved_start + timedelta(days=DEFAULT_HISTORY_WINDOW_DAYS - 1),
        )
    else:
        resolved_start = start
        resolved_end = end

    if resolved_start > resolved_end:
        raise ApiError("INVALID_DATE_RANGE", "Start date must not be after end date.", 400)
    if (resolved_end - resolved_start).days + 1 > MAX_HISTORY_WINDOW_DAYS:
        raise ApiError(
            "HISTORY_RANGE_TOO_LARGE",
            f"History range must not exceed {MAX_HISTORY_WINDOW_DAYS} calendar days.",
            400,
        )
    return resolved_start, resolved_end


__all__ = [
    "DEFAULT_HISTORY_WINDOW_DAYS",
    "MAX_HISTORY_WINDOW_DAYS",
    "MAX_STATE_HISTORY_ROWS",
    "resolve_history_window",
]
