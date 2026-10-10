"""Shared calculation demand from all consumers; no Celery dependency."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from app.core.errors import ApiError
from app.indicators.parameters import (
    IndicatorRequest,
    canonicalize_parameters,
    merge_indicator_requests,
    state_indicator_request,
)
from app.models import AlertRule, Security, TableColumn, WatchTableSymbol

logger = logging.getLogger(__name__)


async def collect_indicator_requests(
    session: Any,
    symbols: Iterable[str],
) -> dict[str, tuple[IndicatorRequest, ...]]:
    """Collect shared calculation demand from all columns using each symbol.

    Columns are configuration, not an ownership boundary for the shared
    indicator data.  We therefore union every table's requests for a symbol;
    the watch-table service still enforces user ownership when projecting the
    result back to a caller.
    """

    normalized_symbols = tuple(str(symbol).strip().upper() for symbol in symbols)
    if not normalized_symbols:
        return {}
    statement = (
        select(
            Security.symbol,
            TableColumn.indicator_type,
            TableColumn.parameters,
            TableColumn.view_mode,
        )
        .join(WatchTableSymbol, WatchTableSymbol.security_id == Security.id)
        .join(TableColumn, TableColumn.watch_table_id == WatchTableSymbol.watch_table_id)
        .where(
            Security.symbol.in_(normalized_symbols),
            TableColumn.indicator_type.is_not(None),
        )
    )
    result = await session.execute(statement)
    raw_by_symbol: dict[str, list[IndicatorRequest]] = {symbol: [] for symbol in normalized_symbols}
    for row in result.all():
        symbol, indicator_type, parameters = row[:3]
        view_mode = row[3] if len(row) > 3 else None
        if not indicator_type or str(indicator_type).upper() == "DIVIDEND_YIELD":
            continue
        raw_parameters = dict(parameters or {})
        raw_parameters = {
            key: value
            for key, value in raw_parameters.items()
            if str(key).strip().lower() != "field"
        }
        try:
            if view_mode == "STATUS":
                canonical = state_indicator_request(indicator_type, raw_parameters).parameters
            else:
                canonical = canonicalize_parameters(
                    indicator_type, raw_parameters, fill_defaults=True
                )
        except ApiError:
            logger.warning(
                "scheduled_indicator_column_ignored",
                extra={
                    "event": "scheduled_indicator_column_ignored",
                    "indicator_type": indicator_type,
                },
            )
            continue
        raw_by_symbol.setdefault(str(symbol), []).append(
            IndicatorRequest(str(indicator_type), canonical)
        )
    alert_rows = await session.execute(
        select(
            Security.symbol,
            AlertRule.condition_type,
            AlertRule.indicator_type,
            AlertRule.parameters,
        )
        .join(AlertRule, AlertRule.security_id == Security.id)
        .where(Security.symbol.in_(normalized_symbols), AlertRule.enabled.is_(True))
    )
    for row in alert_rows.all():
        if len(row) != 4:
            # Compatibility with lightweight test/session adapters that only
            # implement the historical table-column projection.
            continue
        symbol, condition_type, indicator_type, parameters = row
        if not indicator_type or str(indicator_type).upper() == "DIVIDEND_YIELD":
            continue
        raw = dict(parameters or {})
        try:
            canonical = (
                state_indicator_request(indicator_type, raw).parameters
                if condition_type == "STATE"
                else canonicalize_parameters(indicator_type, raw, fill_defaults=True)
            )
        except ApiError:
            continue
        raw_by_symbol.setdefault(str(symbol), []).append(
            IndicatorRequest(str(indicator_type), canonical)
        )

    return {
        symbol: merge_indicator_requests(requests) for symbol, requests in raw_by_symbol.items()
    }
