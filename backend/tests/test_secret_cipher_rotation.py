from __future__ import annotations

import pytest

from app.security.secret_cipher import SecretCipher


def test_secret_cipher_can_read_previous_key_version_during_rotation() -> None:
    old_key = b"o" * 32
    new_key = b"n" * 32
    old = SecretCipher(old_key, key_version="v1")
    ciphertext = old.encrypt("https://example.invalid/secret", aad="user:1")

    rotated = SecretCipher(
        new_key,
        key_version="v2",
        decryption_keys={"v1": old_key},
    )

    assert (
        rotated.decrypt(ciphertext, aad="user:1", key_version="v1")
        == "https://example.invalid/secret"
    )


def test_secret_cipher_rejects_previous_key_that_redefines_current_version() -> None:
    with pytest.raises(ValueError, match="redefine current version"):
        SecretCipher(
            b"n" * 32,
            key_version="v2",
            decryption_keys={"v2": b"o" * 32},
        )
