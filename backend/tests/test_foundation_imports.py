from unittest.mock import patch

from app.db import session as db_session
from app.db.base import Base
from app.db.redis import create_redis_client
from app.db.session import create_engine
from app.tasks.celery_app import celery_app
from app.tasks.scheduler import celery_app as scheduler_app
from app.tasks.worker import celery_app as worker_app


def test_sprint1_auth_models_are_registered_for_migrations() -> None:
    assert set(Base.metadata.tables) >= {"users", "invitations", "refresh_sessions"}


def test_worker_and_scheduler_share_the_celery_application() -> None:
    assert scheduler_app is celery_app
    assert worker_app is celery_app
    assert celery_app.conf.beat_schedule == {}


def test_database_resources_are_not_created_at_module_import() -> None:
    assert not hasattr(db_session, "engine")
    assert not hasattr(db_session, "async_session_factory")


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
