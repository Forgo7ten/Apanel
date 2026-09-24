"""User-scoped notification history queries."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.alert import AlertRepository


class NotificationRepository:
    """Keep notification reads behind a repository named after the resource."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = AlertRepository(session)

    async def list_for_user(self, user_id: int, *, limit: int = 100) -> list:
        return await self._repository.list_notifications(user_id, limit=limit)


__all__ = ["NotificationRepository"]
