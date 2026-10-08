from __future__ import annotations

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
