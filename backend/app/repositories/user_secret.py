"""Persistence for encrypted per-user secrets."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSecret


class UserSecretRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int, secret_type: str) -> UserSecret | None:
        return (
            await self.session.execute(
                select(UserSecret).where(
                    UserSecret.user_id == user_id,
                    UserSecret.secret_type == secret_type,
                )
            )
        ).scalar_one_or_none()

    async def upsert(
        self, user_id: int, secret_type: str, ciphertext: str, key_version: str
    ) -> UserSecret:
        row = await self.get(user_id, secret_type)
        if row is None:
            row = UserSecret(
                user_id=user_id,
                secret_type=secret_type,
                ciphertext=ciphertext,
                key_version=key_version,
            )
            self.session.add(row)
        else:
            row.ciphertext = ciphertext
            row.key_version = key_version
        await self.session.flush()
        return row

    async def delete(self, user_id: int, secret_type: str) -> None:
        row = await self.get(user_id, secret_type)
        if row is not None:
            await self.session.delete(row)
