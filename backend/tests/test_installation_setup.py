"""First-install setup: replay protection, restart safety and file safety."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import verify_password
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import InstallationSetup, User, UserRole
from app.services.auth_service import bootstrap_admin
from app.services.installation_setup import complete_setup, get_setup_status, prepare_setup


async def _setup_factory(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'setup.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_setup_creates_admin_once_and_preserves_completed_state(tmp_path):
    engine, factory = await _setup_factory(tmp_path)
    file = tmp_path / ".apanel-setup-token"
    try:
        async with factory() as session:
            assert await get_setup_status(session) == "NOT_READY"
        async with factory() as session:
            assert await prepare_setup(session, output=file) == "CREATED"
        assert file.stat().st_mode & 0o777 == 0o600
        token = file.read_text().strip()
        assert token.startswith("apanel_setup_")
        async with factory() as session:
            assert await get_setup_status(session) == "PENDING"
        async with factory() as session:
            assert await prepare_setup(session, output=file) == "REUSED"
        assert file.read_text().strip() == token
        async with factory() as session:
            with pytest.raises(ApiError) as exc:
                await complete_setup(
                    session, token="wrong", username="admin",
                    email="admin@example.com", password="secret-password",
                )
            assert exc.value.code == "INVALID_SETUP_TOKEN"
        async with factory() as session:
            admin = await complete_setup(
                session, token=token, username="admin",
                email="admin@example.com", password="secret-password",
            )
            assert admin.role == UserRole.ADMIN
            assert verify_password("secret-password", admin.password_hash)
        async with factory() as session:
            assert await get_setup_status(session) == "COMPLETED"
            state = await session.get(InstallationSetup, 1)
            assert state.token_hash is None and state.completed_at is not None
        async with factory() as session:
            assert await prepare_setup(session, output=file) == "COMPLETED"
        async with factory() as session:
            with pytest.raises(ApiError) as exc:
                await complete_setup(
                    session, token=token, username="another",
                    email="another@example.com", password="another-password",
                )
            assert exc.value.code == "SETUP_ALREADY_COMPLETED"
        async with factory() as session:
            # Even deleting the only administrator does not reopen setup.
            admin = (await session.execute(select(User))).scalar_one()
            await session.delete(admin)
            await session.commit()
        async with factory() as session:
            assert await get_setup_status(session) == "COMPLETED"
        async with factory() as session:
            assert await prepare_setup(session, output=file) == "COMPLETED"
    finally:
        await engine.dispose()


async def test_setup_lost_token_requires_explicit_rotation(tmp_path):
    engine, factory = await _setup_factory(tmp_path)
    file = tmp_path / ".apanel-setup-token"
    try:
        async with factory() as session:
            await prepare_setup(session, output=file)
        old = file.read_text()
        file.unlink()
        async with factory() as session:
            with pytest.raises(RuntimeError, match="--rotate"):
                await prepare_setup(session, output=file)
        async with factory() as session:
            assert await prepare_setup(session, output=file, rotate=True) == "ROTATED"
        assert old != file.read_text()
        async with factory() as session:
            with pytest.raises(ApiError) as exc:
                await complete_setup(
                    session, token=old.strip(), username="admin",
                    email="admin@example.com", password="secret-password",
                )
            assert exc.value.code == "INVALID_SETUP_TOKEN"
    finally:
        await engine.dispose()


async def test_setup_rejects_unsafe_token_file_and_symlink(tmp_path):
    engine, factory = await _setup_factory(tmp_path)
    file = tmp_path / ".apanel-setup-token"
    try:
        async with factory() as session:
            await prepare_setup(session, output=file)
        file.chmod(0o644)
        async with factory() as session:
            with pytest.raises(RuntimeError, match="0600"):
                await prepare_setup(session, output=file)
        file.unlink()
        outside = tmp_path / "outside"
        outside.write_text("do-not-overwrite")
        file.symlink_to(outside)
        async with factory() as session:
            with pytest.raises(RuntimeError):
                await prepare_setup(session, output=file, rotate=True)
        assert outside.read_text() == "do-not-overwrite"
    finally:
        await engine.dispose()


async def test_setup_manual_admin_permanently_closes_setup(tmp_path):
    engine, factory = await _setup_factory(tmp_path)
    try:
        async with factory() as session:
            await bootstrap_admin(
                session, username="manual", email="manual@example.com", password="a-password"
            )
        async with factory() as session:
            result = await prepare_setup(session, output=tmp_path / ".apanel-setup-token")
            assert result == "COMPLETED"
    finally:
        await engine.dispose()


async def test_setup_api_never_exposes_secret_and_validates_payload(tmp_path):
    engine, factory = await _setup_factory(tmp_path)
    file = tmp_path / ".apanel-setup-token"
    async with factory() as session:
        await prepare_setup(session, output=file)
    token = file.read_text().strip()
    app = create_app(Settings(app_env="test"))

    async def database():
        async with factory() as session:
            yield session

    class FakeRedis:
        count = 0

        async def eval(self, *_args):
            self.count += 1
            return self.count

    app.state.redis_client = FakeRedis()
    app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            status_response = await client.get("/api/v1/setup/status")
            assert status_response.status_code == 200
            assert status_response.json()["data"] == {"state": "PENDING"}
            assert token not in status_response.text
            wrong = await client.post("/api/v1/setup/complete", json={
                "token": "apanel_setup_" + "x" * 64,
                "username": "admin", "email": "admin@example.com",
                "password": "secret-password",
            })
            assert wrong.status_code == 403
            created = await client.post("/api/v1/setup/complete", json={
                "token": token, "username": "admin",
                "email": "admin@example.com", "password": "secret-password",
            })
            assert created.status_code == 201
            assert created.json()["data"]["user"]["role"] == "ADMIN"
            assert token not in created.text
            replay = await client.post("/api/v1/setup/complete", json={
                "token": token, "username": "another",
                "email": "another@example.com", "password": "secret-password",
            })
            assert replay.status_code == 409
            app.state.redis_client.count = 30
            limited = await client.post("/api/v1/setup/complete", json={
                "token": token, "username": "another",
                "email": "another@example.com", "password": "secret-password",
            })
            assert limited.status_code == 429
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
