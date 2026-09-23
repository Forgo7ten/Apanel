"""Async Redis client factory."""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings


def create_redis_client(
    redis_url: str | None = None,
    *,
    socket_connect_timeout_seconds: float | None = None,
    socket_timeout_seconds: float | None = None,
) -> Redis:
    """Create a lazy async Redis client with bounded network operations."""

    settings = get_settings()
    return Redis.from_url(
        redis_url or settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=(
            socket_connect_timeout_seconds or settings.redis_socket_connect_timeout_seconds
        ),
        socket_timeout=socket_timeout_seconds or settings.redis_socket_timeout_seconds,
        retry_on_timeout=True,
    )


async def close_redis_client(client: Redis) -> None:
    """Close a Redis client across redis-py 5.x compatible releases."""

    await client.aclose()
