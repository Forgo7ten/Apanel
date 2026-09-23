"""Runtime configuration for the independent market data service."""

import json
from functools import lru_cache
from typing import Annotated
from urllib.parse import unquote, urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
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

DEFAULT_TDX_SERVERS = (
    "119.147.212.81:7709",
    "101.227.73.20:7709",
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

    app_name: str = "Apanel Market Data Service"
    app_version: str = "0.1.0"
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
        description="Runtime environment, for example development or production.",
    )
    environment: str | None = None
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
    market_data_provider: str = Field(default="tdx", min_length=1)
    provider_timeout_seconds: float = Field(default=5.0, gt=0)
    tdx_servers: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_TDX_SERVERS,
        min_length=1,
        validation_alias=AliasChoices("TDX_SERVERS", "TDX_SERVER_LIST"),
        description="Comma-separated or JSON-list TDX host:port endpoints.",
    )
    tdx_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    tdx_retry_attempts: int = Field(default=1, ge=0, le=10)
    tdx_symbol_max_pages: int = Field(default=100, gt=0, le=10000)
    tdx_bar_page_size: int = Field(default=800, gt=0, le=800)
    tdx_bar_max_pages: int = Field(default=64, gt=0, le=10000)
    internal_api_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "INTERNAL_API_TOKEN",
            "INTERNAL_SYNC_TOKEN",
            "MARKET_DATA_INTERNAL_TOKEN",
        ),
        description="Shared secret for internal write endpoints.",
    )
    log_level: str = "INFO"

    @field_validator("tdx_servers", mode="before")
    @classmethod
    def normalize_tdx_servers(cls, value: object) -> tuple[str, ...]:
        """Accept both env-friendly CSV and structured settings values."""

        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("["):
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ValueError("tdx_servers JSON is invalid") from exc
            else:
                value = candidate.split(",")
        if isinstance(value, (list, tuple)):
            values = tuple(str(item).strip() for item in value if str(item).strip())
        else:
            raise TypeError("tdx_servers must be a CSV string or a sequence")
        if not values:
            raise ValueError("tdx_servers must contain at least one endpoint")
        return values

    @model_validator(mode="after")
    def normalize_environment(self) -> "Settings":
        """Make the legacy environment name follow the canonical app_env."""

        if self.environment and self.app_env == "development":
            self.app_env = self.environment
        self.environment = self.app_env
        return self

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
