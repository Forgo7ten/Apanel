"""FastAPI application entry point for the market data service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.redis import close_redis_client, create_redis_client
from app.db.session import create_engine, create_session_factory, dispose_engine
from app.providers.registry import close_provider, create_provider
from app.repositories.health import DatabaseHealthRepository, RedisHealthRepository
from app.repositories.market_data import (
    SqlAlchemyDailyBarRepository,
    SqlAlchemyDividendEventRepository,
    SqlAlchemyQuoteSnapshotRepository,
    SqlAlchemySecurityRepository,
)
from app.schemas.common import ErrorResponse
from app.schemas.market_data import ApiEnvelope
from app.services.health_service import HealthService
from app.services.sync import DailyBarSyncService, SecuritySyncService


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app with injectable infrastructure seams."""

    app_settings = settings or get_settings()
    app_settings.validate_runtime_credentials()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Create and dispose all process resources within the app lifespan."""

        database_engine = create_engine(
            app_settings.database_url,
            echo=app_settings.database_echo,
            connect_timeout_seconds=app_settings.database_connect_timeout_seconds,
            command_timeout_seconds=app_settings.database_command_timeout_seconds,
        )
        redis_client = None
        provider = None
        try:
            redis_client = create_redis_client(
                app_settings.redis_url,
                socket_connect_timeout_seconds=app_settings.redis_socket_connect_timeout_seconds,
                socket_timeout_seconds=app_settings.redis_socket_timeout_seconds,
            )
            health_service = HealthService(
                database_probe=DatabaseHealthRepository(database_engine).ping,
                redis_probe=RedisHealthRepository(redis_client).ping,
                service_name=app_settings.app_name,
                version=app_settings.app_version,
                probe_timeout_seconds=app_settings.health_probe_timeout_seconds,
            )
            session_factory = create_session_factory(database_engine)
            provider = create_provider(app_settings)
            security_repository = SqlAlchemySecurityRepository(session_factory)
            daily_bar_repository = SqlAlchemyDailyBarRepository(session_factory)
            quote_repository = SqlAlchemyQuoteSnapshotRepository(session_factory)
            dividend_repository = SqlAlchemyDividendEventRepository(session_factory)
            app.state.db_engine = database_engine
            app.state.db_session_factory = session_factory
            app.state.redis_client = redis_client
            app.state.provider = provider
            app.state.security_repository = security_repository
            app.state.daily_bar_repository = daily_bar_repository
            app.state.quote_repository = quote_repository
            app.state.dividend_repository = dividend_repository
            app.state.security_sync_service = SecuritySyncService(
                provider=provider,
                repository=security_repository,
            )
            app.state.daily_sync_service = DailyBarSyncService(
                provider=provider,
                repository=daily_bar_repository,
            )
            app.state.health_service = health_service
            yield
        finally:
            try:
                await close_provider(provider)
            finally:
                try:
                    if redis_client is not None:
                        await close_redis_client(redis_client)
                finally:
                    await dispose_engine(database_engine)

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.include_router(api_router)
    return app


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Normalize framework errors without leaking provider/database details."""

    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = detail.get("code", "HTTP_ERROR")
    message = detail.get("message", "Request failed.")
    envelope = ApiEnvelope(
        success=False,
        data=None,
        error=ErrorResponse(code=str(code), message=str(message)),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope.model_dump(mode="json"),
        headers=exc.headers,
    )


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return a stable 422 envelope for malformed query/body input."""

    envelope = ApiEnvelope(
        success=False,
        data=None,
        error=ErrorResponse(
            code="INVALID_REQUEST",
            message="Request parameters are invalid.",
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=envelope.model_dump(),
    )


app = create_app()


__all__ = ["app", "create_app"]
