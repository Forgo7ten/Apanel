"""Regression tests for the Sprint 1 authentication security contract."""

import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException, status
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import Response

from app.api.auth import _delete_refresh_cookie, _set_refresh_cookie
from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.main import create_app


def test_password_hash_uses_argon2id_and_verifies() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("wrong", password_hash)


def test_production_requires_a_non_default_jwt_secret_with_at_least_32_bytes() -> None:
    for secret in ("too-short", "replace_with_a_long_random_jwt_secret_at_least_32_bytes"):
        with pytest.raises(ValueError, match="JWT secret"):
            Settings(
                app_env="production",
                jwt_secret_key=secret,
                database_url="postgresql+asyncpg://apanel:unique@db/apanel",
            ).validate_runtime_credentials()

    settings = Settings(
        app_env="production",
        jwt_secret_key="a" * 48,
        database_url="postgresql+asyncpg://apanel:unique@db/apanel",
    )
    settings.validate_runtime_credentials()


def test_production_rejects_an_explicitly_insecure_refresh_cookie() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret_key="a" * 48,
        database_url="postgresql+asyncpg://apanel:unique@db/apanel",
        refresh_cookie_secure=False,
    )

    with pytest.raises(ValueError, match="secure refresh cookie"):
        settings.validate_runtime_credentials()


def test_access_token_validation_requires_issuer_audience_and_expiry() -> None:
    settings = Settings(
        app_env="test",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
    )
    token = create_access_token(42, settings)

    claims = decode_access_token(token, settings)

    assert claims["sub"] == "42"
    assert claims["iss"] == settings.jwt_issuer
    assert claims["aud"] == settings.jwt_audience
    assert datetime.fromtimestamp(claims["exp"], tz=UTC) > datetime.now(UTC) - timedelta(seconds=1)


def test_access_token_rejects_non_exact_audience_shape() -> None:
    settings = Settings(
        app_env="test",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
    )
    token = jwt.encode(
        {
            "sub": "42",
            "iss": settings.jwt_issuer,
            "aud": [settings.jwt_audience],
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "jti": "test-jti",
            "token_type": "access",
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )

    with pytest.raises(ValueError, match="registered claims"):
        decode_access_token(token, settings)


@pytest.mark.parametrize(
    "headers",
    [
        {"typ": "JOSE"},
        {"typ": "JWT", "alg": "HS384"},
    ],
)
def test_access_token_rejects_non_jwt_or_non_hs256_headers(headers: dict[str, str]) -> None:
    settings = Settings(
        app_env="test",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
    )
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "42",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": "test-jti",
            "token_type": "access",
        },
        settings.jwt_secret_key,
        algorithm=headers.get("alg", "HS256"),
        headers={"typ": headers["typ"]},
    )

    with pytest.raises(ValueError, match="unsupported token header"):
        decode_access_token(token, settings)


@pytest.mark.parametrize(
    "missing_claim", ["iss", "aud", "exp", "sub", "iat", "jti", "token_type"]
)
def test_access_token_rejects_missing_required_claims(missing_claim: str) -> None:
    settings = Settings(
        app_env="test",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
    )
    now = datetime.now(UTC)
    claims = {
        "sub": "42",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": "test-jti",
        "token_type": "access",
    }
    claims.pop(missing_claim)
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")

    with pytest.raises(ValueError):
        decode_access_token(token, settings)


def test_cookie_security_attributes_are_symmetric_on_set_and_delete() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel:unique@db/apanel",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=True,
    )
    set_response = Response()
    _set_refresh_cookie(set_response, "opaque-refresh-token", settings)
    set_cookie = set_response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/v1/auth" in set_cookie

    delete_response = Response()
    _delete_refresh_cookie(delete_response, settings)
    delete_cookie = delete_response.headers["set-cookie"]
    assert "Max-Age=0" in delete_cookie
    assert "Secure" in delete_cookie
    assert "SameSite=lax" in delete_cookie
    assert "Path=/api/v1/auth" in delete_cookie


@pytest.mark.asyncio
async def test_http_exception_detail_is_not_returned_and_validation_fields_are_safe() -> None:
    app = create_app(Settings(app_env="test"))
    request = Request({"type": "http", "method": "GET", "path": "/"})

    http_handler = app.exception_handlers[HTTPException]
    http_response = await http_handler(
        request,
        HTTPException(status_code=500, detail={"secret": "password hash at /srv/users.db"}),
    )
    http_body = json.loads(http_response.body)
    assert http_body == {
        "success": False,
        "error": {"code": "HTTP_ERROR", "message": "Request failed."},
    }
    string_detail_response = await http_handler(
        request,
        HTTPException(status_code=400, detail="database password at /srv/secrets.db"),
    )
    assert "database password" not in string_detail_response.body.decode()

    validation_handler = app.exception_handlers[RequestValidationError]
    validation_response = await validation_handler(
        request,
        RequestValidationError(
            [
                {
                    "loc": ("body", "password"),
                    "type": "string_too_short",
                    "msg": "String should have at least 8 characters",
                    "input": "super-secret-password",
                    "ctx": {"min_length": 8, "secret": "do-not-return"},
                }
            ]
        ),
    )
    validation_body = json.loads(validation_response.body)
    assert validation_response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert validation_body["error"]["details"] == [
        {
            "loc": ["body", "password"],
            "type": "string_too_short",
            "msg": "String should have at least 8 characters",
        }
    ]
    assert "super-secret-password" not in validation_response.body.decode()
    assert "do-not-return" not in validation_response.body.decode()
