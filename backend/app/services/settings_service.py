"""Application service for per-user settings."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import UserSetting
from app.repositories.settings import UserSettingsRepository
from app.schemas.settings import UserSettingsData


class UserSettingsService:
    """Read and merge settings only within the current user's scope."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserSettingsRepository(session)

    async def get(self, user_id: int) -> UserSettingsData:
        setting = await self.repository.get_for_user(user_id)
        if setting is None:
            return UserSettingsData()
        return _settings_data(setting)

    async def update(self, user_id: int, updates: dict[str, Any]) -> UserSettingsData:
        setting = await self.repository.get_for_user(user_id)
        if setting is None:
            setting = UserSetting(user_id=user_id, settings=dict(updates))
            self.session.add(setting)
        else:
            setting.settings = {**setting.settings, **updates}
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("SETTINGS_UPDATE_FAILED", "Settings could not be updated.", 409) from exc
        await self.session.refresh(setting)
        return _settings_data(setting)


def _settings_data(setting: UserSetting) -> UserSettingsData:
    return UserSettingsData(
        settings=dict(setting.settings),
        created_at=setting.created_at,
        updated_at=setting.updated_at,
    )


__all__ = ["UserSettingsService"]
