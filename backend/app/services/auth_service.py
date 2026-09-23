"""Application service for invitation-based authentication."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
)
from app.models import (
    Invitation,
    InvitationStatus,
    RefreshSession,
    User,
    UserRole,
    UserStatus,
)
from app.schemas.auth import InvitationCreateRequest, LoginRequest, RegisterRequest

_DUMMY_PASSWORD_HASH = hash_password("apanel-invalid-login-password")

# PostgreSQL advisory locks are transaction-scoped, so this key serializes
# bootstrap attempts even when the users table is still empty.  SQLite has no
# advisory-lock equivalent; the explicit no-op seam below leaves SQLite tests
# to exercise the same transaction boundary while PostgreSQL provides the
# cross-process guarantee used in production.
BOOTSTRAP_ADMIN_ADVISORY_LOCK_KEY = 4_839_176_021


@dataclass(frozen=True)
class IssuedTokens:
    """Internal token result; the refresh token is never serialized in JSON."""

    access_token: str
    refresh_token: str
    user: User


def normalize_identifier(value: str) -> str:
    """Normalize user-facing identifiers for lookups and ownership checks."""

    return value.strip().casefold()


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for persistence."""

    return datetime.now(UTC)


async def create_invitation(
    session: AsyncSession,
    request: InvitationCreateRequest,
    *,
    admin: User,
    settings: Settings,
) -> InvitationDataResult:
    """Create an invitation and return its raw token exactly once."""

    _require_active_admin(admin)
    raw_token = generate_opaque_token()
    invitation = Invitation(
        email=normalize_identifier(request.email),
        token_hash=hash_opaque_token(raw_token),
        status=InvitationStatus.PENDING,
        created_by=admin.id,
        expires_at=utc_now() + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    session.add(invitation)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("INVALID_REQUEST", "Invitation could not be created.", 400) from exc
    await session.refresh(invitation)
    return InvitationDataResult(invitation=invitation, raw_token=raw_token)


@dataclass(frozen=True)
class InvitationDataResult:
    """Invitation plus its non-persisted raw token."""

    invitation: Invitation
    raw_token: str


async def register_user(
    session: AsyncSession,
    request: RegisterRequest,
) -> User:
    """Atomically lock, validate and consume one invitation."""

    token_hash = hash_opaque_token(request.invite_token)
    now = utc_now()
    invalid_invitation = False
    user: User | None = None
    try:
        async with session.begin():
            invitation = (
                await session.execute(
                    select(Invitation).where(Invitation.token_hash == token_hash).with_for_update()
                )
            ).scalar_one_or_none()
            if invitation is None:
                invalid_invitation = True
            elif invitation.status != InvitationStatus.PENDING:
                invalid_invitation = True
            elif _as_utc(invitation.expires_at) <= now:
                invitation.status = InvitationStatus.EXPIRED
                invalid_invitation = True
            elif request.email and normalize_identifier(request.email) != normalize_identifier(
                invitation.email
            ):
                invalid_invitation = True
            elif invitation is not None:
                user = User(
                    username=request.username.strip(),
                    email=invitation.email,
                    password_hash=hash_password(request.password),
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                    invited_by=invitation.created_by,
                )
                invitation.status = InvitationStatus.ACCEPTED
                invitation.accepted_at = now
                session.add(user)
                try:
                    await session.flush()
                except IntegrityError as exc:
                    raise ApiError(
                        "INVALID_REQUEST", "Username or email is already in use.", 409
                    ) from exc
    except ApiError:
        await session.rollback()
        raise
    if invalid_invitation or user is None:
        raise ApiError("INVALID_INVITATION", "Invitation is invalid or unavailable.", 400)
    return user


async def authenticate_and_issue_tokens(
    session: AsyncSession,
    request: LoginRequest,
    *,
    settings: Settings,
) -> IssuedTokens:
    """Authenticate an active account and create an access/refresh pair."""

    identifier = normalize_identifier(request.username)
    user = (
        await session.execute(
            select(User).where(
                or_(func.lower(User.username) == identifier, func.lower(User.email) == identifier)
            )
        )
    ).scalar_one_or_none()
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(request.password, password_hash)
    if user is None or not password_valid or user.status != UserStatus.ACTIVE:
        raise ApiError("INVALID_CREDENTIALS", "Invalid username or password.", 401)

    return await _issue_tokens(session, user, settings=settings)


async def refresh_tokens(
    session: AsyncSession,
    raw_refresh_token: str,
    *,
    settings: Settings,
) -> IssuedTokens:
    """Rotate a refresh token once; replay revokes every token in its family.

    Every request first resolves its family id, then takes the transaction
    advisory lock for that family before locking or updating a token row.  A
    duplicate request that arrives after the first transaction commits is a
    replay and deliberately revokes the family; the browser's refresh
    coordinator is responsible for collapsing normal same-session concurrent
    refreshes before they reach this replay detector.
    """

    if not raw_refresh_token:
        raise ApiError("INVALID_REFRESH_TOKEN", "Refresh token is invalid or expired.", 401)
    token_hash = hash_opaque_token(raw_refresh_token)
    now = utc_now()
    invalid = False
    access_token: str | None = None
    new_raw_token: str | None = None
    user: User | None = None
    async with session.begin():
        family_id = await _refresh_family_id(session, token_hash)
        if family_id is not None:
            await _lock_refresh_family(session, family_id)
        refresh_session = (
            await session.execute(
                select(RefreshSession)
                .where(RefreshSession.token_hash == token_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if refresh_session is None:
            invalid = True
        elif (
            refresh_session.used_at is not None
            or refresh_session.revoked_at is not None
            or _as_utc(refresh_session.expires_at) <= now
        ):
            await _revoke_family(session, refresh_session.family_id, now)
            invalid = True
        else:
            user = (
                await session.execute(
                    select(User).where(User.id == refresh_session.user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or user.status != UserStatus.ACTIVE:
                await _revoke_family(session, refresh_session.family_id, now)
                invalid = True
            else:
                new_raw_token = generate_opaque_token()
                new_hash = hash_opaque_token(new_raw_token)
                refresh_session.used_at = now
                refresh_session.replaced_by_hash = new_hash
                session.add(
                    RefreshSession(
                        user_id=user.id,
                        token_hash=new_hash,
                        family_id=refresh_session.family_id,
                        issued_at=now,
                        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
                    )
                )
                access_token = create_access_token(user.id, settings)
    if invalid or user is None or access_token is None or new_raw_token is None:
        raise ApiError("INVALID_REFRESH_TOKEN", "Refresh token is invalid or expired.", 401)
    return IssuedTokens(access_token=access_token, refresh_token=new_raw_token, user=user)


async def logout(
    session: AsyncSession,
    raw_refresh_token: str | None,
) -> None:
    """Revoke a refresh-token family; missing/unknown tokens are harmless."""

    if not raw_refresh_token:
        return
    token_hash = hash_opaque_token(raw_refresh_token)
    now = utc_now()
    async with session.begin():
        family_id = await _refresh_family_id(session, token_hash)
        if family_id is not None:
            await _lock_refresh_family(session, family_id)
        refresh_session = (
            await session.execute(
                select(RefreshSession)
                .where(RefreshSession.token_hash == token_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if refresh_session is not None:
            await _revoke_family(session, refresh_session.family_id, now)


async def get_active_user(session: AsyncSession, user_id: int) -> User:
    """Read account status and role from the database for every request."""

    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.status != UserStatus.ACTIVE:
        raise ApiError("AUTHENTICATION_REQUIRED", "Authentication is required.", 401)
    return user


async def bootstrap_admin(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    password: str,
) -> User:
    """Create the first administrator and refuse once any admin exists."""

    async with session.begin():
        await _lock_bootstrap_admin(session)
        existing_admin = (
            await session.execute(
                select(User.id).where(User.role == UserRole.ADMIN).with_for_update()
            )
        ).scalar_one_or_none()
        if existing_admin is not None:
            raise ApiError("ADMIN_ALREADY_EXISTS", "An administrator already exists.", 409)
        user = User(
            username=username.strip(),
            email=normalize_identifier(email),
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ApiError("INVALID_REQUEST", "Username or email is already in use.", 409) from exc
    return user


async def _issue_tokens(session: AsyncSession, user: User, *, settings: Settings) -> IssuedTokens:
    raw_refresh_token = generate_opaque_token()
    now = utc_now()
    session.add(
        RefreshSession(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_refresh_token),
            family_id=str(uuid4()),
            issued_at=now,
            expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("INVALID_REQUEST", "Could not create a session.", 500) from exc
    return IssuedTokens(
        access_token=create_access_token(user.id, settings),
        refresh_token=raw_refresh_token,
        user=user,
    )


async def _revoke_family(session: AsyncSession, family_id: str, now: datetime) -> None:
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.family_id == family_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def _refresh_family_id(session: AsyncSession, token_hash: str) -> str | None:
    """Resolve a token's family without taking a row lock.

    This read must precede the family advisory lock.  The subsequent locked
    read in the same transaction is authoritative and closes the small race
    between this lookup and token rotation.
    """

    return (
        await session.execute(
            select(RefreshSession.family_id).where(RefreshSession.token_hash == token_hash)
        )
    ).scalar_one_or_none()


async def _lock_refresh_family(session: AsyncSession, family_id: str) -> None:
    """Serialize all refresh-family mutations in a deterministic order."""

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _refresh_family_lock_key(family_id)},
        )
    elif dialect_name == "sqlite":
        # SQLite serializes the write transaction at the database level and
        # does not implement transaction advisory locks.  Keeping this seam
        # explicit prevents tests from accidentally depending on PostgreSQL
        # SQL while production still uses the family-scoped lock above.
        return
    else:
        raise RuntimeError(f"Unsupported database dialect for refresh locking: {dialect_name}")


def _refresh_family_lock_key(family_id: str) -> int:
    """Derive a stable signed PostgreSQL advisory-lock key from a family id."""

    return int.from_bytes(
        hashlib.sha256(family_id.encode("utf-8")).digest()[:8], "big", signed=True
    )


async def _lock_bootstrap_admin(session: AsyncSession) -> None:
    """Take the first-admin lock, with an explicit SQLite compatibility seam."""

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": BOOTSTRAP_ADMIN_ADVISORY_LOCK_KEY},
        )
    elif dialect_name == "sqlite":
        return
    else:
        raise RuntimeError(
            f"Unsupported database dialect for admin bootstrap locking: {dialect_name}"
        )


def _require_active_admin(user: User) -> None:
    if user.status != UserStatus.ACTIVE or user.role != UserRole.ADMIN:
        raise ApiError("FORBIDDEN", "Administrator access is required.", 403)


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamp result to the UTC comparison domain."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "BOOTSTRAP_ADMIN_ADVISORY_LOCK_KEY",
    "InvitationDataResult",
    "IssuedTokens",
    "authenticate_and_issue_tokens",
    "bootstrap_admin",
    "create_invitation",
    "get_active_user",
    "logout",
    "normalize_identifier",
    "refresh_tokens",
    "register_user",
]
