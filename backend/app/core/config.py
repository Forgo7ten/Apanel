"""Runtime configuration for the backend service."""

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

KNOWN_DEFAULT_JWT_SECRETS = frozenset(
    {
        "development-only-jwt-secret-change-me-at-least-32-bytes",
        "change-me",
        "changeme",
        "secret",
        "your-secret-key",
        "replace_with_a_long_random_jwt_secret_at_least_32_bytes",
    }
)


class Settings(BaseSettings):
    """Environment-backed settings with safe local development defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    app_name: str = "Apanel Backend"
    app_version: str = "0.1.0"
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
        description="Runtime environment, for example development or production.",
    )
    # Keep the descriptive name available to callers while app_env is the
    # canonical setting and APP_ENV is the canonical container variable.
    environment: str | None = None
    database_url: str = Field(
        default="postgresql+asyncpg://apanel:apanel@localhost:5432/apanel",
        description="SQLAlchemy async database URL.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL used by health checks and Celery.",
    )
    celery_broker_url: str | None = Field(
        default=None,
        description="Optional broker override; defaults to redis_url.",
    )
    celery_result_backend: str | None = Field(
        default=None,
        description="Optional result backend override; defaults to redis_url.",
    )
    database_echo: bool = False
    health_probe_timeout_seconds: float = Field(default=2.0, gt=0)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    database_command_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    market_data_service_url: str = Field(
        default="http://market-data-service:8001",
        validation_alias=AliasChoices("MARKET_DATA_SERVICE_URL", "MARKET_DATA_URL"),
    )
    internal_api_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "INTERNAL_API_TOKEN",
            "INTERNAL_SYNC_TOKEN",
            "MARKET_DATA_INTERNAL_TOKEN",
        ),
        description="Shared secret for backend-to-market-data internal writes.",
    )
    market_data_request_timeout_seconds: float = Field(default=10.0, gt=0)
    quote_refresh_interval_minutes: int = Field(
        default=5,
        ge=5,
        le=10,
        validation_alias=AliasChoices(
            "CELERY_QUOTE_REFRESH_MINUTES",
            "QUOTE_REFRESH_INTERVAL_MINUTES",
        ),
    )
    eod_pipeline_hour: int = Field(
        default=15,
        ge=15,
        le=23,
        validation_alias=AliasChoices(
            "EOD_PIPELINE_HOUR",
            "CELERY_EOD_HOUR",
            "CELERY_EOD_PIPELINE_HOUR",
        ),
    )
    eod_pipeline_minute: int = Field(
        default=20,
        ge=0,
        le=59,
        validation_alias=AliasChoices(
            "EOD_PIPELINE_MINUTE",
            "CELERY_EOD_MINUTE",
            "CELERY_EOD_PIPELINE_MINUTE",
        ),
    )
    scheduler_lock_ttl_seconds: int = Field(default=3600, gt=0)
    scheduler_retry_max_attempts: int = Field(default=3, ge=0, le=20)
    scheduler_retry_backoff_seconds: int = Field(default=60, gt=0)
    log_level: str = "INFO"
    timezone: str = Field(
        default="Asia/Shanghai",
        validation_alias=AliasChoices("TIMEZONE", "CELERY_TIMEZONE"),
    )
    jwt_secret_key: str = Field(
        default="development-only-jwt-secret-change-me-at-least-32-bytes",
        validation_alias=AliasChoices("JWT_SECRET_KEY", "JWT_SECRET"),
    )
    jwt_issuer: str = Field(default="apanel", validation_alias=AliasChoices("JWT_ISSUER"))
    jwt_audience: str = Field(default="apanel-web", validation_alias=AliasChoices("JWT_AUDIENCE"))
    access_token_ttl_seconds: int = Field(default=900, gt=0)
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 30, gt=0)
    invitation_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, gt=0)
    refresh_cookie_name: str = "refresh_token"
    refresh_cookie_secure: bool | None = None

    @model_validator(mode="after")
    def normalize_environment(self) -> "Settings":
        """Make the legacy environment name follow the canonical app_env."""

        if self.environment and self.app_env == "development":
            self.app_env = self.environment
        self.environment = self.app_env
        return self

    def validate_database_credentials(self) -> None:
        """Reject known development database credentials in production.

        Migrations and scheduler processes use this narrow check.  They must
        not need the application's JWT signing secret merely to connect to the
        database.
        """

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

    def validate_auth_credentials(self) -> None:
        """Require strong authentication credentials for the API process."""

        if self.app_env.casefold() != "production":
            return

        if (
            self.jwt_secret_key in KNOWN_DEFAULT_JWT_SECRETS
            or len(self.jwt_secret_key.encode("utf-8")) < 32
        ):
            raise ValueError(
                "Production requires a JWT secret with at least 32 bytes; "
                "set JWT_SECRET_KEY to a unique secret."
            )
        if self.refresh_cookie_secure is False:
            raise ValueError(
                "Production requires a secure refresh cookie; "
                "set REFRESH_COOKIE_SECURE=true or leave it unset."
            )

    def validate_runtime_credentials(self) -> None:
        """Validate all credentials required by the backend API process."""

        self.validate_database_credentials()
        self.validate_auth_credentials()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
