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
    security_master_fallback_provider: str = Field(
        default="akshare",
        min_length=1,
        validation_alias=AliasChoices(
            "SECURITY_MASTER_FALLBACK_PROVIDER",
            "SECURITY_FALLBACK_PROVIDER",
        ),
        description="Security metadata fallback name; use none to disable fallback.",
    )
    akshare_security_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        validation_alias=AliasChoices(
            "AKSHARE_SECURITY_TIMEOUT_SECONDS",
            "SECURITY_MASTER_FALLBACK_TIMEOUT_SECONDS",
            "AKSHARE_TIMEOUT_SECONDS",
        ),
        description="Total timeout for the complete AKShare security batch.",
    )
    akshare_min_stock_count: int = Field(
        default=1000,
        gt=0,
        validation_alias=AliasChoices(
            "AKSHARE_MIN_STOCK_COUNT",
            "SECURITY_MASTER_MIN_STOCK_COUNT",
            "SECURITY_MASTER_FALLBACK_MIN_STOCK_COUNT",
        ),
    )
    akshare_min_etf_count: int = Field(
        default=1,
        gt=0,
        validation_alias=AliasChoices(
            "AKSHARE_MIN_ETF_COUNT",
            "SECURITY_MASTER_MIN_ETF_COUNT",
            "SECURITY_MASTER_FALLBACK_MIN_ETF_COUNT",
        ),
    )
    tdx_servers: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_TDX_SERVERS,
        min_length=1,
        max_length=4,
        validation_alias=AliasChoices("TDX_SERVERS", "TDX_SERVER_LIST"),
        description="Comma-separated or JSON-list TDX host:port endpoints.",
    )
    tdx_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    tdx_retry_attempts: int = Field(default=1, ge=0, le=10)
    tdx_symbol_timeout_seconds: float = Field(default=60.0, gt=0)
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
            if not candidate:
                raise ValueError("tdx_servers must not be empty")
            if candidate.startswith("["):
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise ValueError("tdx_servers JSON is invalid") from exc
            else:
                value = candidate.split(",")
        if isinstance(value, (list, tuple)):
            values_list: list[str] = []
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError("tdx_servers endpoint must not be empty")
                endpoint = item.strip()
                _validate_tdx_server_endpoint(endpoint)
                if endpoint not in values_list:
                    values_list.append(endpoint)
            values = tuple(values_list)
        else:
            raise TypeError("tdx_servers must be a CSV string or a sequence")
        if not values:
            raise ValueError("tdx_servers must contain at least one endpoint")
        if len(values) > 4:
            raise ValueError("tdx_servers must contain at most 4 endpoints")
        return values

    @field_validator("security_master_fallback_provider")
    @classmethod
    def normalize_security_master_fallback_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("security_master_fallback_provider must not be empty")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def map_security_master_aliases(cls, value: object) -> object:
        """Accept stable semantic aliases when constructing Settings directly."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        aliases = {
            "security_master_fallback_timeout_seconds": "akshare_security_timeout_seconds",
            "akshare_timeout_seconds": "akshare_security_timeout_seconds",
            "security_master_min_stock_count": "akshare_min_stock_count",
            "security_master_min_etf_count": "akshare_min_etf_count",
        }
        for alias, canonical in aliases.items():
            if alias in data and canonical not in data:
                data[canonical] = data[alias]
        return data

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


def _validate_tdx_server_endpoint(value: str) -> None:
    """Validate the same host/port forms accepted by the blocking adapter."""

    candidate = value.strip()
    if not candidate:
        raise ValueError("tdx_servers endpoint must not be empty")
    if candidate.startswith("["):
        if candidate.endswith("]") and candidate[1:-1].strip():
            return
        host, separator, port = candidate.rpartition("]:")
        if not separator or not host[1:] or not port:
            raise ValueError(f"tdx_servers endpoint is invalid: {value!r}")
        _validate_tdx_server_port(port, value)
        return
    if candidate.count(":") > 1:
        raise ValueError(f"tdx_servers endpoint must bracket IPv6 hosts: {value!r}")
    if ":" not in candidate:
        return
    host, port = candidate.rsplit(":", 1)
    if not host.strip():
        raise ValueError(f"tdx_servers endpoint host must not be empty: {value!r}")
    _validate_tdx_server_port(port, value)


def _validate_tdx_server_port(value: str, endpoint: str) -> None:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"tdx_servers endpoint port is invalid: {endpoint!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"tdx_servers endpoint port is invalid: {endpoint!r}")
