"""Encrypt/decrypt user secrets at the application boundary."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSecret, UserSetting
from app.repositories.user_secret import UserSecretRepository
from app.security.secret_cipher import SecretCipher

FEISHU_WEBHOOK_SECRET = "FEISHU_WEBHOOK"


@dataclass(frozen=True, slots=True)
class UserSecretMigrationAudit:
    legacy_plaintext_count: int
    encrypted_count: int
    legacy_without_encrypted_count: int

    @property
    def release_b_ready(self) -> bool:
        return self.legacy_plaintext_count == 0


async def audit_user_secrets(session: AsyncSession) -> UserSecretMigrationAudit:
    """Count migration state without returning or logging secret values."""

    settings = list((await session.execute(select(UserSetting))).scalars())
    legacy_user_ids: set[int] = set()
    for row in settings:
        payload = row.settings if isinstance(row.settings, dict) else {}
        nested = payload.get("notification_settings")
        if not isinstance(nested, dict):
            nested = payload.get("notification")
        raw = nested.get("feishu_webhook") if isinstance(nested, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            raw = payload.get("feishu_webhook")
        if isinstance(raw, str) and raw.strip():
            legacy_user_ids.add(int(row.user_id))

    encrypted_user_ids = set(
        int(item)
        for item in (
            await session.execute(
                select(UserSecret.user_id).where(UserSecret.secret_type == FEISHU_WEBHOOK_SECRET)
            )
        ).scalars()
    )
    return UserSecretMigrationAudit(
        legacy_plaintext_count=len(legacy_user_ids),
        encrypted_count=len(encrypted_user_ids),
        legacy_without_encrypted_count=len(legacy_user_ids - encrypted_user_ids),
    )


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


__all__ = [
    "FEISHU_WEBHOOK_SECRET",
    "UserSecretMigrationAudit",
    "UserSecretService",
    "audit_user_secrets",
]
