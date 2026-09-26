from unittest.mock import AsyncMock, call, patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


class ClosingProvider:
    async def get_symbols(self):
        return ()

    async def get_quote(self, symbol):
        raise NotImplementedError

    async def get_daily_bars(self, symbol, start, end, adjustment="none"):
        raise NotImplementedError

    async def get_dividends(self, symbol):
        return ()


def test_lifespan_closes_the_app_scoped_resources() -> None:
    app = create_app(Settings(app_env="test"))
    assert not hasattr(app.state, "db_engine")
    assert not hasattr(app.state, "redis_client")

    with (
        patch("app.main.close_redis_client", new_callable=AsyncMock) as close_redis,
        patch("app.main.dispose_engine", new_callable=AsyncMock) as dispose_engine,
    ):
        with TestClient(app):
            assert app.state.db_engine is not None
            assert app.state.redis_client is not None

    close_redis.assert_awaited_once_with(app.state.redis_client)
    dispose_engine.assert_awaited_once_with(app.state.db_engine)


def test_lifespan_initializes_and_closes_provider_resource() -> None:
    app = create_app(Settings(app_env="test"))
    provider = ClosingProvider()
    with (
        patch("app.main.create_provider", return_value=provider) as create_provider,
        patch("app.main.create_security_master_provider", return_value=provider) as create_security,
        patch("app.main.close_provider", new_callable=AsyncMock) as close_provider,
        patch("app.main.close_redis_client", new_callable=AsyncMock),
        patch("app.main.dispose_engine", new_callable=AsyncMock),
    ):
        with TestClient(app):
            assert app.state.provider is provider

    create_provider.assert_called_once()
    create_security.assert_called_once()
    close_provider.assert_awaited_once_with(provider)


def test_lifespan_uses_security_composite_only_for_security_sync() -> None:
    app = create_app(Settings(app_env="test"))
    tdx_provider = ClosingProvider()
    security_provider = ClosingProvider()
    with (
        patch("app.main.create_provider", return_value=tdx_provider),
        patch("app.main.create_security_master_provider", return_value=security_provider),
        patch("app.main.close_provider", new_callable=AsyncMock),
        patch("app.main.close_redis_client", new_callable=AsyncMock),
        patch("app.main.dispose_engine", new_callable=AsyncMock),
    ):
        with TestClient(app):
            assert app.state.provider is tdx_provider
            assert app.state.security_provider is security_provider
            assert app.state.security_sync_service._provider is security_provider
            assert app.state.daily_sync_service._provider is tdx_provider
            assert app.state.quote_sync_service._provider is tdx_provider
            assert app.state.dividend_sync_service._provider is tdx_provider


def test_lifespan_closes_tdx_and_security_composite_once_each() -> None:
    app = create_app(Settings(app_env="test"))
    tdx_provider = ClosingProvider()
    security_provider = ClosingProvider()
    with (
        patch("app.main.create_provider", return_value=tdx_provider),
        patch("app.main.create_security_master_provider", return_value=security_provider),
        patch("app.main.close_provider", new_callable=AsyncMock) as close_provider,
        patch("app.main.close_redis_client", new_callable=AsyncMock),
        patch("app.main.dispose_engine", new_callable=AsyncMock),
    ):
        with TestClient(app):
            pass

    assert close_provider.await_args_list == [call(tdx_provider), call(security_provider)]
