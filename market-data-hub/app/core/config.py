"""Runtime configuration for the independent market data hub."""

import json
from functools import lru_cache
from typing import Annotated
from urllib.parse import unquote, urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

KNOWN_DEFAULT_DATABASE_PASSWORDS = frozenset(
    {
        "apanel",
        "apanel_local_only_change_me",
        "change_me",
        "changeme",
        "password",
        "postgres",
        "replace_with_a_long_url_safe_random_password",
    }
)


class Settings(BaseSettings):
    """Environment-backed settings with local development defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    app_name: str = "Apanel Market Data Hub"
    app_version: str = "0.1.0"
    app_env: str = Field(
        default="development",
        validation_alias="APP_ENV",
        description="Runtime environment, for example development or production.",
    )
    database_url: str = Field(
        default="postgresql+asyncpg://apanel:apanel@localhost:5432/apanel",
        description="SQLAlchemy async database URL for future market storage.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for future quote cache.",
    )
    database_echo: bool = False
    health_probe_timeout_seconds: float = Field(default=2.0, gt=0)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    database_command_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    eltdx_hosts: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        validation_alias="ELTDX_HOSTS",
        description=(
            "Optional CSV/JSON list of eltdx host:port endpoints; empty uses eltdx defaults."
        ),
    )
    eltdx_timeout_seconds: float = Field(default=5.0, gt=0)
    eltdx_probe_hosts: bool = True
    eltdx_pool_size: int | None = Field(default=None, gt=0)
    eltdx_server_count: int | None = Field(default=None, gt=0)
    eltdx_connections_per_server: int | None = Field(default=None, gt=0)
    eltdx_heartbeat_interval_seconds: float | None = Field(default=30.0, gt=0)
    eltdx_bar_page_size: int = Field(default=800, gt=0, le=800)
    eltdx_bar_max_pages: int = Field(default=64, gt=0, le=10000)
    eltdx_quote_batch_size: int = Field(default=50, gt=0, le=1000)
    market_data_sync_concurrency: int = Field(default=4, gt=0, le=64)
    market_data_sync_max_symbols_per_request: int = Field(default=100, gt=0, le=1000)
    security_master_fallback_provider: str = Field(
        default="akshare",
        min_length=1,
        validation_alias="SECURITY_MASTER_FALLBACK_PROVIDER",
        description="Security metadata fallback name; use none to disable fallback.",
    )
    akshare_security_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        validation_alias="AKSHARE_SECURITY_TIMEOUT_SECONDS",
        description="Total timeout for the complete AKShare security batch.",
    )
    akshare_min_stock_count: int = Field(
        default=1000,
        gt=0,
        validation_alias="AKSHARE_MIN_STOCK_COUNT",
    )
    akshare_min_etf_count: int = Field(
        default=1,
        gt=0,
        validation_alias="AKSHARE_MIN_ETF_COUNT",
    )
    internal_api_token: str | None = Field(
        default=None,
        validation_alias="INTERNAL_API_TOKEN",
        description="Shared secret for internal write endpoints.",
    )
    log_level: str = "INFO"

    @field_validator("eltdx_hosts", mode="before")
    @classmethod
    def normalize_eltdx_hosts(cls, value: object) -> tuple[str, ...]:
        """Accept an empty value, CSV, JSON list, or sequence of host:port values."""

        if value is None or value == "":
            return ()
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return ()
            if candidate.startswith("["):
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ValueError("eltdx_hosts JSON is invalid") from exc
            else:
                value = candidate.split(",")
        if not isinstance(value, (list, tuple)):
            raise TypeError("eltdx_hosts must be a CSV string or sequence")
        hosts: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("eltdx host must not be empty")
            endpoint = item.strip()
            _validate_host_port(endpoint, field="eltdx_hosts")
            if endpoint not in hosts:
                hosts.append(endpoint)
        return tuple(hosts)

    @field_validator("security_master_fallback_provider")
    @classmethod
    def normalize_security_master_fallback_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("security_master_fallback_provider must not be empty")
        return normalized

    def validate_runtime_credentials(self) -> None:
        """Reject known development database credentials in production."""

        if self.app_env.casefold() != "production":
            return

        try:
            password = unquote(urlsplit(self.database_url).password or "")
        except ValueError:
            password = ""
        if not password or password in KNOWN_DEFAULT_DATABASE_PASSWORDS:
            raise ValueError(
                "Production requires a non-default PostgreSQL password; "
                "set POSTGRES_PASSWORD/DATABASE_URL to a unique secret."
            )
        if not self.internal_api_token or not self.internal_api_token.strip():
            raise ValueError(
                "Production requires INTERNAL_API_TOKEN for protected internal sync endpoints."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()


def _validate_host_port(value: str, *, field: str) -> None:
    candidate = value.strip()
    if not candidate or ":" not in candidate:
        raise ValueError(f"{field} endpoint must be host:port: {value!r}")
    host, port_text = candidate.rsplit(":", 1)
    if not host.strip():
        raise ValueError(f"{field} endpoint host must not be empty: {value!r}")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"{field} endpoint port is invalid: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{field} endpoint port is invalid: {value!r}")
