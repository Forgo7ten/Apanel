"""SQLAlchemy 2 async engine foundation for future market storage."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def create_engine(
    database_url: str | None = None,
    *,
    echo: bool | None = None,
    connect_timeout_seconds: float | None = None,
    command_timeout_seconds: float | None = None,
) -> AsyncEngine:
    """Create an async engine without opening a connection."""

    settings = get_settings()
    resolved_url = database_url or settings.database_url
    connect_args: dict[str, float] = {}
    if resolved_url.startswith("postgresql"):
        connect_args = {
            "timeout": connect_timeout_seconds or settings.database_connect_timeout_seconds,
            "command_timeout": command_timeout_seconds or settings.database_command_timeout_seconds,
        }
    return create_async_engine(
        resolved_url,
        echo=settings.database_echo if echo is None else echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(
    database_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create the application-scoped async session factory.

    Repositories own one transaction per public write operation.  Sessions
    are intentionally not shared across requests or sync symbols.
    """

    return async_sessionmaker(
        bind=database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def ping_database(database_engine: AsyncEngine) -> None:
    """Run a readiness query against PostgreSQL."""

    async with database_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose_engine(database_engine: AsyncEngine) -> None:
    """Dispose all pooled connections during shutdown."""

    await database_engine.dispose()
