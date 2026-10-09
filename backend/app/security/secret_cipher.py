"""Application-level authenticated encryption for user-owned secrets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings


class SecretCipher:
    def __init__(
        self,
        key: bytes,
        *,
        key_version: str,
        decryption_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        if len(key) != 32:
            raise ValueError("secret encryption key must contain 32 bytes")
        self._cipher = AESGCM(key)
        self.key_version = key_version
        self._decryptors = {key_version: self._cipher}
        for version, previous_key in (decryption_keys or {}).items():
            if str(version) == key_version:
                raise ValueError("previous secret key versions must not redefine current version")
            if len(previous_key) != 32:
                raise ValueError("secret encryption key must contain 32 bytes")
            self._decryptors[str(version)] = AESGCM(previous_key)

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
        previous: dict[str, bytes] = {}
        if selected.app_secrets_previous_keys:
            try:
                payload = json.loads(selected.app_secrets_previous_keys)
                if not isinstance(payload, dict):
                    raise TypeError
                previous = {
                    str(version): base64.urlsafe_b64decode(str(encoded).encode("ascii"))
                    for version, encoded in payload.items()
                }
            except Exception as exc:
                raise ValueError(
                    "APP_SECRETS_PREVIOUS_KEYS must be a JSON object of URL-safe base64 keys"
                ) from exc
        return cls(
            key,
            key_version=selected.app_secrets_key_version,
            decryption_keys=previous,
        )

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str, *, aad: str, key_version: str | None = None) -> str:
        version = key_version or self.key_version
        cipher = self._decryptors.get(version)
        if cipher is None:
            raise ValueError(f"secret encryption key version is unavailable: {version}")
        payload = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        return cipher.decrypt(payload[:12], payload[12:], aad.encode("utf-8")).decode("utf-8")
