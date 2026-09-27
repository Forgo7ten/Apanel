"""Encrypt/decrypt user secrets at the application boundary."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_secret import UserSecretRepository
from app.security.secret_cipher import SecretCipher

FEISHU_WEBHOOK_SECRET = "FEISHU_WEBHOOK"


class UserSecretService:
    def __init__(self, session: AsyncSession, *, cipher: SecretCipher | None = None) -> None:
        self.session = session
        self.repository = UserSecretRepository(session)
        self.cipher = cipher or SecretCipher.from_settings()

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
        return self.cipher.decrypt(row.ciphertext, aad=self._aad(user_id, FEISHU_WEBHOOK_SECRET))

    async def clear_feishu_webhook(self, user_id: int) -> None:
        await self.repository.delete(user_id, FEISHU_WEBHOOK_SECRET)

    async def configured(self, user_id: int) -> bool:
        return await self.repository.get(user_id, FEISHU_WEBHOOK_SECRET) is not None
