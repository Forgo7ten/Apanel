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


def test_security_master_fallback_defaults_to_akshare_with_conservative_thresholds() -> None:
    settings = Settings()

    assert settings.security_master_fallback_provider == "akshare"
    assert settings.akshare_security_timeout_seconds == 90
    assert settings.akshare_min_stock_count == 1000
    assert settings.akshare_min_etf_count == 1


def test_security_master_fallback_settings_are_configurable() -> None:
    settings = Settings(
        security_master_fallback_provider="none",
        akshare_security_timeout_seconds=17,
        akshare_min_stock_count=12,
        akshare_min_etf_count=3,
    )

    assert settings.security_master_fallback_provider == "none"
    assert settings.akshare_security_timeout_seconds == 17
    assert settings.akshare_min_stock_count == 12
    assert settings.akshare_min_etf_count == 3


def test_proxy_environment_values_are_not_exposed_by_application_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_proxy = "http://proxy-user:proxy-password@proxy.internal:8123"
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.setenv(variable, secret_proxy)

    settings = Settings(_env_file=None)

    assert secret_proxy not in repr(settings)
    assert secret_proxy not in repr(settings.model_dump())
