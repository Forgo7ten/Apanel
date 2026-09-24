"""Persistence queries for per-user settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSetting


class UserSettingsRepository:
    """All settings operations are scoped by the authenticated user id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: int) -> UserSetting | None:
        return (
            await self.session.execute(select(UserSetting).where(UserSetting.user_id == user_id))
        ).scalar_one_or_none()


__all__ = ["UserSettingsRepository"]
