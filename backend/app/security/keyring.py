"""One secret-key parser shared by startup validation and runtime encryption.

Versions are kept byte-for-byte for safe ongoing encryption-key rotation.
Whitespace-normalized collisions and duplicate JSON keys are rejected, not silently
renamed or overwritten. Key material is deliberately excluded from repr().
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class SecretKeyRing:
    version: str
    key: bytes = field(repr=False)
    previous: Mapping[str, bytes] = field(repr=False)


def decode_secret_key(value: str, name: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError(f"{name} must be URL-safe base64.")
    try:
        raw = value.encode("ascii")
        raw += b"=" * (-len(raw) % 4)
        decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"{name} must be URL-safe base64.") from exc
    if len(decoded) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes.")
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value.rstrip("="):
        raise ValueError(f"{name} must be canonical URL-safe base64.")
    return decoded


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("APP_SECRETS_PREVIOUS_KEYS contains duplicate versions.")
        result[key] = value
    return result


def parse_secret_keyring(
    encoded_key: str | None,
    version: str,
    encoded_previous: str | None,
    *,
    fallback_key: bytes | None = None,
) -> SecretKeyRing:
    if encoded_key:
        key = decode_secret_key(encoded_key, "APP_SECRETS_KEY")
    elif fallback_key is not None and len(fallback_key) == 32:
        key = fallback_key
    else:
        raise ValueError("APP_SECRETS_KEY is required for encrypted user secrets.")
    if not isinstance(version, str) or not version.strip() or len(version) > 16:
        raise ValueError("APP_SECRETS_KEY_VERSION must not be empty or exceed 16 characters.")
    previous: dict[str, bytes] = {}
    seen = {version.strip()}
    if encoded_previous:
        try:
            payload = json.loads(encoded_previous, object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "APP_SECRETS_PREVIOUS_KEYS must be a JSON object with unique versions."
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError("APP_SECRETS_PREVIOUS_KEYS must be a JSON object.")
        for old_version, encoded in payload.items():
            normalized = old_version.strip()
            if not normalized or len(old_version) > 16:
                raise ValueError("Previous secret key versions must not be empty or too long.")
            if normalized == version.strip():
                raise ValueError("Previous secret keys must not redefine the current key version.")
            if normalized in seen:
                raise ValueError("Previous secret key versions must be unique after normalization.")
            seen.add(normalized)
            previous[old_version] = decode_secret_key(encoded, "APP_SECRETS_PREVIOUS_KEYS")
    return SecretKeyRing(version, key, MappingProxyType(previous))
