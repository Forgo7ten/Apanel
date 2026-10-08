import pytest

from app.core.config import Settings


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
    assert settings.environment == "development"


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
        app_secrets_key="worker-secret-key",
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
