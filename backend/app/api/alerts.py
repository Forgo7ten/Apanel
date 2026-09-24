"""Authenticated alert-rule controllers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.alerts import AlertRuleCreateRequest, AlertRuleData, AlertRuleUpdateRequest
from app.schemas.common import SuccessResponse
from app.schemas.watch import DeleteData
from app.services.alert_service import AlertService

router = APIRouter()


@router.get("/alerts", response_model=SuccessResponse[list[AlertRuleData]])
async def list_alerts(
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[AlertRuleData]]:
    return SuccessResponse(data=await AlertService(session).list(user.id))


@router.post(
    "/alerts",
    response_model=SuccessResponse[AlertRuleData],
    status_code=status.HTTP_201_CREATED,
)
async def create_alert(
    payload: AlertRuleCreateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[AlertRuleData]:
    return SuccessResponse(data=await AlertService(session).create(user.id, payload))


@router.put("/alerts/{alert_id}", response_model=SuccessResponse[AlertRuleData])
async def update_alert(
    alert_id: int,
    payload: AlertRuleUpdateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[AlertRuleData]:
    return SuccessResponse(data=await AlertService(session).update(user.id, alert_id, payload))


@router.delete("/alerts/{alert_id}", response_model=SuccessResponse[DeleteData])
async def delete_alert(
    alert_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[DeleteData]:
    await AlertService(session).delete(user.id, alert_id)
    return SuccessResponse(data=DeleteData())


__all__ = ["router"]
