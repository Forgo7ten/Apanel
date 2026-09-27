"""Application-level authenticated encryption for user-owned secrets."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings


class SecretCipher:
    def __init__(self, key: bytes, *, key_version: str) -> None:
        if len(key) != 32:
            raise ValueError("secret encryption key must contain 32 bytes")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SecretCipher:
        selected = settings or get_settings()
        raw = selected.app_secrets_key
        if raw:
            try:
                key = base64.urlsafe_b64decode(raw.encode("ascii"))
            except Exception as exc:
                raise ValueError("APP_SECRETS_KEY must be URL-safe base64") from exc
        elif selected.app_env.casefold() == "production":
            raise ValueError("APP_SECRETS_KEY is required in production")
        else:
            # Stable local/test fallback only; production never reaches this path.
            key = hashlib.sha256(selected.jwt_secret_key.encode("utf-8")).digest()
        return cls(key, key_version=selected.app_secrets_key_version)

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str, *, aad: str) -> str:
        payload = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        return self._cipher.decrypt(payload[:12], payload[12:], aad.encode("utf-8")).decode("utf-8")
