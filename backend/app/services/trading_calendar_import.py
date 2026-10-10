"""Operator-confirmed annual exchange calendar import into existing schema.

Never infer the future exchange calendar from weekdays or eltdx past bars.
The operator must check all three official exchange announcements and provide
an explicit closure schedule for the complete calendar year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TradingCalendar

OFFICIAL_EXCHANGES = {
    "SH": "sse.com.cn",
    "SZ": "szse.cn",
    "BJ": "bse.cn",
}


@dataclass(frozen=True, slots=True)
class AnnualCalendar:
    year: int
    sources: dict[str, str]
    closures: tuple[tuple[date, date], ...]

    def expected_days(self) -> dict[date, bool]:
        start = date(self.year, 1, 1)
        end = date(self.year, 12, 31)
        days: dict[date, bool] = {}
        current = start
        while current <= end:
            closed = current.weekday() >= 5 or any(
                first <= current <= last for first, last in self.closures
            )
            days[current] = not closed
            current += timedelta(days=1)
        return days


def parse_annual_calendar(payload: Any) -> AnnualCalendar:
    if not isinstance(payload, dict) or set(payload) != {
        "year",
        "market",
        "sources",
        "closed_ranges",
        "operator_confirmed",
    }:
        raise ValueError("calendar manifest has an invalid top-level shape")
    year = payload["year"]
    if isinstance(year, bool) or not isinstance(year, int) or not 2025 <= year <= 2100:
        raise ValueError("calendar year must be an integer from 2025 to 2100")
    if payload["market"] != "CN":
        raise ValueError("only the shared CN calendar is supported")
    if payload["operator_confirmed"] is not True:
        raise ValueError("official exchange announcements must be confirmed by an operator")
    sources = payload["sources"]
    if not isinstance(sources, dict) or set(sources) != set(OFFICIAL_EXCHANGES):
        raise ValueError("all three official SH/SZ/BJ source URLs are required")
    for exchange, suffix in OFFICIAL_EXCHANGES.items():
        url = sources[exchange]
        if not isinstance(url, str):
            raise ValueError("exchange source URLs must be HTTPS")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or hostname not in {suffix, "www." + suffix}
            or not parsed.path.startswith("/")
            or parsed.path == "/"
        ):
            raise ValueError("source URL must point to an official exchange HTTPS announcement")
    closed = payload["closed_ranges"]
    if not isinstance(closed, list) or not closed:
        raise ValueError("closed_ranges must list the official closure periods")
    ranges: list[tuple[date, date]] = []
    for period in closed:
        if (
            not isinstance(period, list)
            or len(period) != 2
            or not all(isinstance(value, str) for value in period)
        ):
            raise ValueError("each closure is a [start, end] ISO date pair")
        try:
            first, last = (date.fromisoformat(value) for value in period)
        except ValueError as exc:
            raise ValueError("closure dates must be ISO dates") from exc
        if first.year != year or last.year != year or first > last:
            raise ValueError("closed range must be within the selected year")
        ranges.append((first, last))
    ranges.sort()
    for before, after in zip(ranges, ranges[1:], strict=False):
        if before[1] >= after[0]:
            raise ValueError("overlapping annual closure ranges are not permitted")
    return AnnualCalendar(year, dict(sources), tuple(ranges))


async def import_annual_calendar(
    session: AsyncSession, calendar: AnnualCalendar, *, replace_existing: bool = False
) -> dict[str, int]:
    """Apply complete-year expected statuses in one transaction.

    Preserve already-confirmed actual observations. Conflicting expected/actual
    data yields UNKNOWN (never a fabricated OPEN/CLOSED or deletion).
    """
    expected = calendar.expected_days()
    rows = (
        (
            await session.execute(
                select(TradingCalendar)
                .where(
                    TradingCalendar.market == "CN",
                    TradingCalendar.trade_date >= date(calendar.year, 1, 1),
                    TradingCalendar.trade_date <= date(calendar.year, 12, 31),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    existing = {row.trade_date: row for row in rows}
    if not replace_existing:
        conflict = next(
            (
                day
                for day, row in existing.items()
                if row.expected_open is not None and row.expected_open != expected[day]
            ),
            None,
        )
        if conflict is not None:
            raise ValueError(
                "calendar expected schedule differs on "
                + conflict.isoformat()
                + "; use --replace-existing only after verifying an official correction"
            )
    provenance = {
        "year": calendar.year,
        "sources": calendar.sources,
        "operator_confirmed": True,
    }
    counts = {"created": 0, "updated": 0, "actual_conflicts": 0}
    for day, open_expected in expected.items():
        row = existing.get(day)
        if row is None:
            row = TradingCalendar(market="CN", trade_date=day, actual_open=None)
            session.add(row)
            counts["created"] += 1
        elif (
            row.expected_open == open_expected
            and row.source == "OFFICIAL_ANNUAL"
            and row.source_metadata == provenance
        ):
            if row.actual_open is not None and row.actual_open != open_expected:
                counts["actual_conflicts"] += 1
            continue
        else:
            counts["updated"] += 1
        row.expected_open = open_expected
        row.status = (
            "UNKNOWN"
            if row.actual_open is not None and row.actual_open != open_expected
            else ("OPEN" if open_expected else "CLOSED")
        )
        if row.status == "UNKNOWN":
            counts["actual_conflicts"] += 1
        row.source = "OFFICIAL_ANNUAL"
        row.source_metadata = provenance
    await session.commit()
    return counts
