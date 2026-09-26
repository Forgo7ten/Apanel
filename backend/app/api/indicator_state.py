"""Read-only indicator and state API controllers."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import IndicatorState
from app.schemas.common import SuccessResponse
from app.schemas.indicator_state import (
    IndicatorHistoryData,
    StateData,
    StateHistoryData,
)
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
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[StateHistoryData]:
    states = await StateService(session).history(
        symbol,
        start=start,
        end=end,
        adjustment=adjustment,
    )
    return SuccessResponse(data=StateHistoryData(items=[_state_data(item) for item in states]))


def _state_data(state: IndicatorState) -> StateData:
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
        trade_date=state.trade_date,
        metadata=metadata,
    )


__all__ = ["router"]
