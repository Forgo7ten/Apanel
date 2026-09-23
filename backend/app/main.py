"""FastAPI application entry point for the Apanel backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import __version__
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.redis import close_redis_client, create_redis_client
from app.db.session import create_engine, dispose_engine
from app.repositories.health import DatabaseHealthRepository, RedisHealthRepository
from app.services.health_service import HealthService


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application instance with explicit infrastructure seams."""

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
        try:
            db_session_factory = async_sessionmaker(database_engine, expire_on_commit=False)
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
            app.state.db_engine = database_engine
            app.state.db_session_factory = db_session_factory
            app.state.redis_client = redis_client
            app.state.health_service = health_service
            yield
        finally:
            try:
                if redis_client is not None:
                    await close_redis_client(redis_client)
            finally:
                await dispose_engine(database_engine)

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version or __version__,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.include_router(api_router)
    return app


app = create_app()

__all__ = ["app", "create_app"]
