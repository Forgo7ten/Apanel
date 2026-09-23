"""Integration tests for the public Sprint 1 authentication seams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.security import create_access_token, hash_opaque_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import Invitation, InvitationStatus, RefreshSession, User, UserRole, UserStatus


@pytest_asyncio.fixture
async def auth_context(
    tmp_path,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
    settings = Settings(
        app_env="test",
        database_url=database_url,
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=False,
    )
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings)
    app.state.db_session_factory = session_factory
    app.state.db_engine = engine

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_admin(session_factory: async_sessionmaker[AsyncSession]) -> User:
    async with session_factory() as session:
        admin = User(
            username="Admin",
            email="ADMIN@example.com",
            password_hash=hash_password("admin-password"),
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        return admin


async def test_invite_register_login_and_me_use_the_unified_contract(auth_context) -> None:
    client, session_factory = auth_context
    admin = await _seed_admin(session_factory)

    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin@example.com", "password": "admin-password"}
    )
    assert login.status_code == 200
    assert login.json()["success"] is True
    assert "refresh_token" not in login.json()["data"]
    assert "HttpOnly" in login.headers["set-cookie"]
    access_token = login.json()["data"]["access_token"]

    invitation_response = await client.post(
        "/api/v1/auth/invitations",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"email": "user@example.com"},
    )
    assert invitation_response.status_code == 201
    invitation_body = invitation_response.json()
    assert invitation_body["success"] is True
    raw_invitation_token = invitation_body["data"]["token"]
    async with session_factory() as session:
        invitation = (
            await session.execute(
                select(Invitation).where(Invitation.id == invitation_body["data"]["id"])
            )
        ).scalar_one()
        assert invitation.token_hash == hash_opaque_token(raw_invitation_token)
        assert raw_invitation_token not in invitation.token_hash

    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": raw_invitation_token,
            "username": "new-user",
            "password": "user-password",
        },
    )
    assert registration.status_code == 201
    assert registration.json()["data"]["status"] == "ACTIVE"

    user_login = await client.post(
        "/api/v1/auth/login", json={"username": "NEW-USER", "password": "user-password"}
    )
    assert user_login.status_code == 200
    user_access_token = user_login.json()["data"]["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {user_access_token}"}
    )
    assert me.status_code == 200
    assert me.json()["data"]["email"] == "user@example.com"
    assert me.json()["data"]["role"] == "USER"

    assert admin.id != me.json()["data"]["id"]


async def test_login_failures_are_indistinguishable_and_disabled_users_cannot_login(
    auth_context,
) -> None:
    client, session_factory = auth_context
    await _seed_admin(session_factory)

    for credentials in (
        {"username": "missing", "password": "wrong-password"},
        {"username": "Admin", "password": "wrong-password"},
    ):
        response = await client.post("/api/v1/auth/login", json=credentials)
        assert response.status_code == 401
        assert response.json() == {
            "success": False,
            "error": {"code": "INVALID_CREDENTIALS", "message": "Invalid username or password."},
        }

    async with session_factory() as session:
        admin = (await session.execute(select(User))).scalar_one()
        admin.status = UserStatus.DISABLED
        await session.commit()
    response = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_disabling_a_user_invalidates_an_already_issued_access_token(auth_context) -> None:
    client, session_factory = auth_context
    admin = await _seed_admin(session_factory)
    login = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
    )
    access_token = login.json()["data"]["access_token"]

    async with session_factory() as session:
        user = await session.get(User, admin.id)
        assert user is not None
        user.status = UserStatus.DISABLED
        await session.commit()

    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


async def test_refresh_rotation_replay_revokes_the_family_and_logout_is_idempotent(
    auth_context,
) -> None:
    client, session_factory = auth_context
    await _seed_admin(session_factory)
    await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
    )
    old_refresh_token = client.cookies.get("refresh_token")
    assert old_refresh_token
    first_refresh = await client.post("/api/v1/auth/refresh")
    assert first_refresh.status_code == 200
    new_refresh_token = client.cookies.get("refresh_token")
    assert new_refresh_token and new_refresh_token != old_refresh_token

    # A delayed presentation of the already-rotated token is a true replay,
    # not a normal concurrent request coordinated by the browser.
    await asyncio.sleep(0.05)
    replay = await client.post("/api/v1/auth/refresh", cookies={"refresh_token": old_refresh_token})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    family_refresh = await client.post(
        "/api/v1/auth/refresh", cookies={"refresh_token": new_refresh_token}
    )
    assert family_refresh.status_code == 401

    first_logout = await client.post("/api/v1/auth/logout")
    second_logout = await client.post("/api/v1/auth/logout")
    assert first_logout.status_code == second_logout.status_code == 200
    async with session_factory() as session:
        rows = (await session.execute(select(RefreshSession))).scalars().all()
        assert rows
        assert all(row.revoked_at is not None for row in rows)


async def test_database_role_is_authoritative_for_admin_routes(auth_context) -> None:
    client, session_factory = auth_context
    admin = await _seed_admin(session_factory)
    access_token = create_access_token(
        admin.id,
        Settings(app_env="test", jwt_secret_key="test-secret-that-is-at-least-32-bytes-long"),
    )
    async with session_factory() as session:
        admin = await session.get(User, admin.id)
        assert admin is not None
        admin.role = UserRole.USER
        await session.commit()
    response = await client.post(
        "/api/v1/auth/invitations",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"email": "other@example.com"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_expired_invitation_is_consumed_as_expired_without_creating_a_user(
    auth_context,
) -> None:
    client, session_factory = auth_context
    admin = await _seed_admin(session_factory)
    expired_token = "expired-token-value"
    async with session_factory() as session:
        invitation = Invitation(
            email="expired@example.com",
            token_hash=hash_opaque_token(expired_token),
            status=InvitationStatus.PENDING,
            created_by=admin.id,
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        session.add(invitation)
        await session.commit()
    response = await client.post(
        "/api/v1/auth/register",
        json={"invite_token": expired_token, "username": "expired", "password": "password"},
    )
    assert response.status_code == 400
    async with session_factory() as session:
        stored = (
            await session.execute(
                select(Invitation).where(Invitation.email == "expired@example.com")
            )
        ).scalar_one()
        assert stored.status == InvitationStatus.EXPIRED
        assert (
            await session.execute(select(User).where(User.username == "expired"))
        ).scalar_one_or_none() is None
