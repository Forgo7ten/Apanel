"""Isolation-level regression tests for PostgreSQL auth coordination.

Set ``APANEL_TEST_POSTGRES_URL`` to a throwaway PostgreSQL database URL to run
these tests.  They intentionally skip in the normal SQLite test suite so the
unit/integration suite never mutates a developer's database by accident.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.db.base import Base
from app.models import RefreshSession, User, UserRole, UserStatus
from app.services.auth_service import bootstrap_admin, refresh_tokens


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.environ.get("APANEL_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("set APANEL_TEST_POSTGRES_URL to run PostgreSQL isolation tests")
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.fail("APANEL_TEST_POSTGRES_URL must use postgresql+asyncpg://")

    engine = create_async_engine(database_url, pool_size=5, max_overflow=0)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def postgres_session_factory(
    postgres_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield async_sessionmaker(postgres_engine, expire_on_commit=False)


async def test_bootstrap_admin_concurrent_processes_create_exactly_one_admin(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    barrier = asyncio.Barrier(2)

    async def attempt(index: int) -> tuple[str, str | int]:
        async with postgres_session_factory() as session:
            await barrier.wait()
            try:
                user = await bootstrap_admin(
                    session,
                    username=f"admin-{index}",
                    email=f"admin-{index}@example.com",
                    password="admin-password",
                )
            except ApiError as error:
                return "error", error.code
            return "success", user.id

    results = await asyncio.gather(attempt(1), attempt(2))

    assert [result[0] for result in results].count("success") == 1
    assert [result[0] for result in results].count("error") == 1
    assert next(result[1] for result in results if result[0] == "error") == "ADMIN_ALREADY_EXISTS"

    async with postgres_session_factory() as session:
        admins = (
            await session.execute(select(User).where(User.role == UserRole.ADMIN))
        ).scalars().all()
        assert len(admins) == 1


async def test_refresh_same_token_concurrency_is_one_success_and_a_family_replay(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(
        app_env="test",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
    )
    raw_token = generate_opaque_token()
    family_id = "00000000-0000-0000-0000-000000000001"
    async with postgres_session_factory() as session:
        user = User(
            username="refresh-user",
            email="refresh-user@example.com",
            password_hash="not-used-in-this-test",
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        session.add(
            RefreshSession(
                user_id=user.id,
                token_hash=hash_opaque_token(raw_token),
                family_id=family_id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()

    barrier = asyncio.Barrier(2)

    async def attempt() -> str:
        async with postgres_session_factory() as session:
            await barrier.wait()
            try:
                await refresh_tokens(session, raw_token, settings=settings)
            except ApiError as error:
                return error.code
            return "SUCCESS"

    results = await asyncio.gather(attempt(), attempt())
    assert results.count("SUCCESS") == 1
    assert results.count("INVALID_REFRESH_TOKEN") == 1

    async with postgres_session_factory() as session:
        rows = (
            await session.execute(
                select(RefreshSession).where(RefreshSession.family_id == family_id)
            )
        ).scalars().all()
        assert rows
        assert all(row.revoked_at is not None for row in rows)
