"""Read-only indicator and state API controllers."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import SuccessResponse
from app.schemas.indicator_state import (
    IndicatorHistoryData,
    StateData,
    StateHistoryData,
)
from app.schemas.state_row import StateRow
from app.services.indicator_service import IndicatorService
from app.services.state_service import StateService

router = APIRouter()


@router.get("/securities/{symbol}/indicators")
async def get_indicators(
    symbol: str,
    adjustment: str | None = Query(default="qfq", alias="adjust"),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[dict[str, Any]]:
    """Return the latest indicator values grouped by indicator type."""

    data = await IndicatorService(session).latest_data(symbol, adjustment=adjustment)
    return SuccessResponse(data=data)


@router.get(
    "/securities/{symbol}/indicators/history",
    response_model=SuccessResponse[IndicatorHistoryData],
)
async def get_indicator_history(
    symbol: str,
    start: date | None = Query(default=None),  # noqa: B008
    end: date | None = Query(default=None),  # noqa: B008
    adjustment: str | None = Query(default="qfq", alias="adjust"),  # noqa: B008
    parameter_key: str | None = Query(default=None),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[IndicatorHistoryData]:
    data = await IndicatorService(session).history_data(
        symbol,
        start=start,
        end=end,
        adjustment=adjustment,
        parameter_key=parameter_key,
    )
    return SuccessResponse(data=data)


@router.get(
    "/securities/{symbol}/states",
    response_model=SuccessResponse[list[StateData]],
)
async def get_current_states(
    symbol: str,
    adjustment: str | None = Query(default="qfq", alias="adjust"),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[StateData]]:
    states = await StateService(session).current(symbol, adjustment=adjustment)
    return SuccessResponse(data=[_state_data(item) for item in states])


@router.get(
    "/securities/{symbol}/states/history",
    response_model=SuccessResponse[StateHistoryData],
)
async def get_state_history(
    symbol: str,
    start: date | None = Query(default=None),  # noqa: B008
    end: date | None = Query(default=None),  # noqa: B008
    adjustment: str | None = Query(default="qfq", alias="adjust"),  # noqa: B008
    state_code: str | None = Query(default=None),  # noqa: B008
    parameter_key: str | None = Query(default=None),  # noqa: B008
    indicator_type: str | None = Query(default=None),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[StateHistoryData]:
    states = await StateService(session).history(
        symbol,
        start=start,
        end=end,
        adjustment=adjustment,
        state_code=state_code,
        parameter_key=parameter_key,
        indicator_type=indicator_type,
    )
    return SuccessResponse(data=StateHistoryData(items=[_state_data(item) for item in states]))


@router.get("/securities/{symbol}/states/events")
async def get_state_events(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    adjustment: str = Query(default="qfq", alias="adjust"),  # noqa: B008
    state_code: str | None = None,
    parameter_key: str | None = None,
    after_seq: int = Query(default=0, ge=0),  # noqa: B008
    limit: int = Query(default=500, ge=1, le=1000),  # noqa: B008
    generation: int | None = Query(default=None, ge=0),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[dict[str, Any]]:
    from app.core.errors import ApiError
    from app.repositories.state_event import StateEventRepository
    from app.services.analysis_control import publication_is_current, published_stream_for_read
    from app.services.history_window import resolve_history_window
    from app.services.indicator_service import _validate_indicator_adjustment

    adjustment = _validate_indicator_adjustment(adjustment)
    security = await StateService(session)._get_security(symbol)
    stream = await published_stream_for_read(session, security.id, adjustment)
    if stream is None or stream.state_through is None:
        raise ApiError("STATE_EVENTS_NOT_READY", "State event history is not initialized.", 409)
    if not publication_is_current(stream):
        raise ApiError("ANALYSIS_REBUILD_PENDING", "Analysis is being rebuilt.", 503)
    if generation is not None and generation != stream.state_generation:
        raise ApiError(
            "STATE_EVENT_GENERATION_CHANGED", "Event history changed; restart pagination.", 409
        )
    current = (
        await StateEventRepository(session).current_many([security.id], adjustment=adjustment)
    )[security.id]
    latest = max((row.trade_date for row in current), default=stream.state_through)
    if latest is None:
        raise ApiError("STATE_DATA_NOT_FOUND", "No State history is available.", 404)
    first, last = resolve_history_window(start=start, end=end, latest_date=latest)
    rows = await StateEventRepository(session).events(
        security.id,
        adjustment,
        start=first,
        end=last,
        state_code=state_code.upper() if state_code else None,
        parameter_key=parameter_key,
        after_seq=after_seq,
        limit=limit + 1,
    )
    more = len(rows) > limit
    items = [
        {
            "id": row.id,
            "stream_seq": row.stream_seq,
            "state_code": row.state_code,
            "parameter_key": row.parameter_key,
            "parameters": row.parameters,
            "adjust_type": row.adjust_type,
            "trade_date": row.trade_date,
            "status": row.status,
            "event_kind": row.event_kind,
            "since_date": row.since_date,
            "observed_from": row.observed_from,
            "since_is_exact": row.since_is_exact,
            "source_revision": row.source_revision,
            "is_restatement": row.is_restatement,
            "metadata": row.metadata_json,
        }
        for row in rows[:limit]
    ]
    return SuccessResponse(
        data={
            "items": items,
            "generation": stream.state_generation,
            "source_revision": stream.published_revision,
            "next_cursor": items[-1]["stream_seq"] if more else None,
        }
    )


def _state_data(state: StateRow) -> StateData:
    metadata = dict(state.metadata_json)
    return StateData(
        state_id=state.state_code,
        state_code=state.state_code,
        title=str(metadata.get("name", state.state_code)),
        level=str(metadata.get("level", "INFO")),
        indicator_type=state.indicator_type,
        status=state.status,
        active=bool(metadata.get("active", state.status == "ACTIVE")),
        transition=bool(metadata.get("transition", False)),
        since_date=getattr(state, "since_date", None),
        observed_from=getattr(state, "observed_from", None),
        since_is_exact=bool(getattr(state, "since_is_exact", False)),
        trade_date=state.trade_date,
        parameters=dict(state.parameters or {}),
        parameter_key=state.parameter_key,
        adjust_type=state.adjust_type,
        metadata=metadata,
    )


__all__ = ["router"]
