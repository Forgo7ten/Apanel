import base64
import json

import pytest

from app.core.config import Settings


def _secret_key(byte: bytes = b"k") -> str:
    return base64.urlsafe_b64encode(byte * 32).decode("ascii")


def test_production_rejects_known_default_database_password() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel:apanel_local_only_change_me@db/apanel",
    )

    with pytest.raises(ValueError, match="non-default PostgreSQL password"):
        settings.validate_runtime_credentials()


def test_development_keeps_local_database_password_usable() -> None:
    settings = Settings(
        app_env="development",
        database_url="postgresql+asyncpg://apanel:apanel@db/apanel",
    )

    settings.validate_runtime_credentials()
    assert settings.app_env == "development"


def test_production_rejects_a_missing_database_password() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel@db/apanel",
    )

    with pytest.raises(ValueError, match="non-default PostgreSQL password"):
        settings.validate_runtime_credentials()


def test_infrastructure_validation_does_not_require_the_jwt_secret() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel:unique@db/apanel",
    )

    settings.validate_database_credentials()

    with pytest.raises(ValueError, match="JWT secret"):
        settings.validate_auth_credentials()


def test_production_worker_requires_market_data_token() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel:unique@db/apanel",
        app_secrets_key=_secret_key(),
        internal_api_token=None,
    )

    with pytest.raises(ValueError, match="INTERNAL_API_TOKEN"):
        settings.validate_worker_credentials()


def test_production_worker_requires_user_secret_master_key() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel:unique@db/apanel",
        app_secrets_key=None,
        internal_api_token="worker-internal-token",
    )

    with pytest.raises(ValueError, match="APP_SECRETS_KEY"):
        settings.validate_worker_credentials()


def test_development_worker_keeps_optional_local_secrets() -> None:
    settings = Settings(app_env="development")

    settings.validate_worker_credentials()


def test_production_notification_credentials_validate_rotation_keyring() -> None:
    settings = Settings(
        app_env="production",
        app_secrets_key=_secret_key(b"n"),
        app_secrets_key_version="v2",
        app_secrets_previous_keys=json.dumps({"v1": _secret_key(b"o")}),
    )

    settings.validate_notification_credentials()


@pytest.mark.parametrize(
    ("current", "previous", "message"),
    [
        ("not-base64!!!", None, "APP_SECRETS_KEY"),
        (base64.urlsafe_b64encode(b"short").decode("ascii"), None, "32 bytes"),
        (_secret_key(), "not-json", "JSON object"),
        (
            _secret_key(b"n"),
            json.dumps({"v1": base64.urlsafe_b64encode(b"short").decode("ascii")}),
            "32 bytes",
        ),
        (
            _secret_key(b"n"),
            json.dumps({"v2": _secret_key(b"o")}),
            "must not redefine",
        ),
    ],
)
def test_production_notification_credentials_fail_fast_on_invalid_keyring(
    current: str, previous: str | None, message: str
) -> None:
    settings = Settings(
        app_env="production",
        app_secrets_key=current,
        app_secrets_key_version="v2",
        app_secrets_previous_keys=previous,
    )

    with pytest.raises(ValueError, match=message):
        settings.validate_notification_credentials()
