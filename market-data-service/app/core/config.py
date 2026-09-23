"""Runtime configuration for the independent market data service."""

from functools import lru_cache
from urllib.parse import unquote, urlsplit

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    log_level: str = "INFO"

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
