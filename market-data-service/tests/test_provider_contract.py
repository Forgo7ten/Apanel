from unittest.mock import patch

from greenlet import getcurrent
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.redis import create_redis_client
from app.db.session import create_engine
from app.providers.base import MarketDataProvider


def test_market_data_provider_is_abstract() -> None:
    assert MarketDataProvider.__abstractmethods__ == {
        "get_symbols",
        "get_quote",
        "get_daily_bars",
        "get_dividends",
    }


def test_database_engine_passes_bounded_client_timeouts() -> None:
    with patch("app.db.session.create_async_engine", return_value=object()) as create:
        create_engine(
            "postgresql+asyncpg://user:secret@db/apanel",
            connect_timeout_seconds=1.25,
            command_timeout_seconds=2.5,
        )

    create.assert_called_once_with(
        "postgresql+asyncpg://user:secret@db/apanel",
        echo=False,
        pool_pre_ping=True,
        connect_args={"timeout": 1.25, "command_timeout": 2.5},
    )


def test_async_database_engine_runtime_dependencies_are_available() -> None:
    """The service startup path can create an async engine in a fresh runtime."""
    assert getcurrent() is not None

    engine = create_engine("postgresql+asyncpg://user:secret@db/apanel")
    try:
        assert isinstance(engine, AsyncEngine)
    finally:
        engine.sync_engine.dispose()


def test_redis_client_passes_bounded_socket_timeouts() -> None:
    with patch("app.db.redis.Redis.from_url", return_value=object()) as create:
        client = create_redis_client(
            "redis://cache:6379/0",
            socket_connect_timeout_seconds=1.25,
            socket_timeout_seconds=2.5,
        )

    assert client is create.return_value
    create.assert_called_once_with(
        "redis://cache:6379/0",
        decode_responses=True,
        socket_connect_timeout=1.25,
        socket_timeout=2.5,
        retry_on_timeout=True,
    )
