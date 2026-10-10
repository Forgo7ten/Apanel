"""Application-level authenticated encryption for user-owned secrets."""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings
from app.security.keyring import parse_secret_keyring


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
        fallback = None
        if selected.app_env.casefold() != "production":
            fallback = hashlib.sha256(selected.jwt_secret_key.encode("utf-8")).digest()
        ring = parse_secret_keyring(
            selected.app_secrets_key,
            selected.app_secrets_key_version,
            selected.app_secrets_previous_keys,
            fallback_key=fallback,
        )
        return cls(ring.key, key_version=ring.version, decryption_keys=ring.previous)

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
