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


def test_production_rejects_missing_internal_sync_token() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://apanel:unique-secret@db/apanel",
    )

    with pytest.raises(ValueError, match="INTERNAL_API_TOKEN"):
        settings.validate_runtime_credentials()
