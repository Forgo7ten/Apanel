"""Password and token primitives for the authentication service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

_PASSWORD_HASHER = PasswordHasher()


class InvalidAccessToken(ValueError):
    """Raised when an access token does not satisfy the strict JWT contract."""


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""

    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without exposing hash-parser details to callers."""

    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def hash_opaque_token(token: str) -> str:
    """Return the only representation of an opaque token suitable for storage."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_opaque_token() -> str:
    """Generate a high-entropy URL-safe token for invitations and cookies."""

    return secrets.token_urlsafe(48)


def create_access_token(user_id: int, settings: Settings) -> str:
    """Issue a short-lived JWT with explicit issuer, audience and expiry."""

    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
        "jti": str(uuid4()),
        "token_type": "access",
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm="HS256",
        headers={"typ": "JWT"},
    )


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Decode and strictly validate an access JWT.

    In particular, the accepted algorithm and type are checked before decode,
    while issuer, audience and required time claims are checked by PyJWT.
    """

    try:
        header = jwt.get_unverified_header(token)
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise InvalidAccessToken("invalid access token") from exc
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise InvalidAccessToken("unsupported token header")

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iss", "aud", "sub", "iat", "jti"]},
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise InvalidAccessToken("invalid access token") from exc

    if (
        claims.get("iss") != settings.jwt_issuer
        or claims.get("aud") != settings.jwt_audience
        or not isinstance(claims.get("exp"), (int, float))
        or isinstance(claims.get("exp"), bool)
    ):
        raise InvalidAccessToken("invalid registered claims")
    if claims.get("token_type") != "access":
        raise InvalidAccessToken("invalid token type")
    if (
        not isinstance(claims.get("sub"), str)
        or not claims["sub"].isdigit()
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
    ):
        raise InvalidAccessToken("invalid subject")
    return claims
