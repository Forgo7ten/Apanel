"""Authenticated notification history controller."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.alerts import NotificationData
from app.schemas.common import SuccessResponse
from app.services.alert_service import AlertService

router = APIRouter()


@router.get("/notifications", response_model=SuccessResponse[list[NotificationData]])
async def list_notifications(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[NotificationData]]:
    return SuccessResponse(data=await AlertService(session).notifications(user.id, limit=limit))


@router.post(
    "/notifications/{notification_id}/retry",
    response_model=SuccessResponse[NotificationData],
)
async def retry_notification(
    notification_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[NotificationData]:
    return SuccessResponse(
        data=await AlertService(session).retry_notification(user.id, notification_id)
    )


__all__ = ["router"]
