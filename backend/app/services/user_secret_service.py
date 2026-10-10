"""Encrypt/decrypt user secrets at the application boundary."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories.user_secret import UserSecretRepository
from app.security.secret_cipher import SecretCipher

FEISHU_WEBHOOK_SECRET = "FEISHU_WEBHOOK"


class UserSecretService:
    def __init__(self, session: AsyncSession, *, cipher: SecretCipher | None = None) -> None:
        self.session = session
        self.repository = UserSecretRepository(session)
        self.cipher = cipher or SecretCipher.from_settings()

    async def lock_owner(self, user_id: int) -> None:
        # The parent exists even before either settings or secret rows exist.
        await self.session.execute(select(User.id).where(User.id == user_id).with_for_update())

    def _aad(self, user_id: int, secret_type: str) -> str:
        return f"apanel:user:{user_id}:{secret_type}:v1"

    async def set_feishu_webhook(self, user_id: int, webhook: str) -> None:
        ciphertext = self.cipher.encrypt(webhook, aad=self._aad(user_id, FEISHU_WEBHOOK_SECRET))
        await self.repository.upsert(
            user_id, FEISHU_WEBHOOK_SECRET, ciphertext, self.cipher.key_version
        )

    async def get_feishu_webhook(self, user_id: int) -> str | None:
        row = await self.repository.get(user_id, FEISHU_WEBHOOK_SECRET)
        if row is None:
            return None
        return self.cipher.decrypt(
            row.ciphertext,
            aad=self._aad(user_id, FEISHU_WEBHOOK_SECRET),
            key_version=row.key_version,
        )

    async def clear_feishu_webhook(self, user_id: int) -> None:
        await self.repository.delete(user_id, FEISHU_WEBHOOK_SECRET)

    async def configured(self, user_id: int) -> bool:
        return await self.repository.get(user_id, FEISHU_WEBHOOK_SECRET) is not None


__all__ = [
    "FEISHU_WEBHOOK_SECRET",
    "UserSecretService",
]
