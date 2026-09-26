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


def test_tdx_servers_are_deduplicated_in_order_and_symbol_timeout_is_separate() -> None:
    settings = Settings(
        tdx_servers="first.test:7709, second.test:7709,first.test:7709",
        tdx_symbol_timeout_seconds=42,
    )

    assert settings.tdx_servers == ("first.test:7709", "second.test:7709")
    assert settings.tdx_symbol_timeout_seconds == 42


def test_tdx_server_configuration_rejects_empty_invalid_and_more_than_four_endpoints() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        Settings(tdx_servers="first.test:7709,,second.test:7709")
    with pytest.raises(ValueError, match="port"):
        Settings(tdx_servers="first.test:not-a-port")
    with pytest.raises(ValueError, match="at most 4"):
        Settings(
            tdx_servers=(
                "one.test:7709",
                "two.test:7709",
                "three.test:7709",
                "four.test:7709",
                "five.test:7709",
            )
        )
