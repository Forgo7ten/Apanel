"""Infrastructure readiness probes."""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import ping_database


class DatabaseHealthRepository:
    """Probe PostgreSQL through the configured SQLAlchemy engine."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> None:
        await ping_database(self._engine)


class RedisHealthRepository:
    """Probe Redis through the configured async client."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    async def ping(self) -> None:
        await self._client.ping()
