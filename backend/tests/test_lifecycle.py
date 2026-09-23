from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_lifespan_closes_the_app_scoped_resources() -> None:
    app = create_app(Settings(app_env="test"))
    assert not hasattr(app.state, "db_engine")
    assert not hasattr(app.state, "db_session_factory")
    assert not hasattr(app.state, "redis_client")

    with (
        patch("app.main.close_redis_client", new_callable=AsyncMock) as close_redis,
        patch("app.main.dispose_engine", new_callable=AsyncMock) as dispose_engine,
    ):
        with TestClient(app):
            assert app.state.db_session_factory.kw["bind"] is app.state.db_engine

    close_redis.assert_awaited_once_with(app.state.redis_client)
    dispose_engine.assert_awaited_once_with(app.state.db_engine)
