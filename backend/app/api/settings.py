"""Authenticated user settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.common import SuccessResponse
from app.schemas.settings import UserSettingsData, UserSettingsUpdateRequest
from app.services.settings_service import UserSettingsService

router = APIRouter()


@router.get("/settings", response_model=SuccessResponse[UserSettingsData])
async def get_settings(
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[UserSettingsData]:
    return SuccessResponse(data=await UserSettingsService(session).get(user.id))


@router.put("/settings", response_model=SuccessResponse[UserSettingsData])
async def update_settings(
    payload: UserSettingsUpdateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[UserSettingsData]:
    raw = payload.model_dump(exclude_unset=True)
    nested = raw.pop("settings", None)
    updates = dict(nested) if isinstance(nested, dict) else {}
    updates.update(raw)
    return SuccessResponse(data=await UserSettingsService(session).update(user.id, updates))


__all__ = ["router"]
