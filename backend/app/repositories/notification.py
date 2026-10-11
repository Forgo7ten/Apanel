"""User-scoped notification history queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.alert import AlertRepository


class NotificationRepository:
    """Keep notification reads behind a repository named after the resource."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = AlertRepository(session)

    async def list_for_user(self, user_id: int, *, limit: int = 100) -> list:
        return await self._repository.list_notifications(user_id, limit=limit)

    async def history_page_for_user(
        self, user_id: int, *, limit: int, before: tuple[datetime, int] | None = None
    ) -> list:
        return await self._repository.notification_history_page(user_id, limit=limit, before=before)


__all__ = ["NotificationRepository"]
