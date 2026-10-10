"""Durable, one-time Web setup, shared by the CLI and HTTP entry points.

The bootstrap CLI alone sees the host token file. The HTTP service only sees
its hash in PostgreSQL and can never read the host repository.
"""

from __future__ import annotations

import hmac
import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.models import InstallationSetup, User, UserRole
from app.services.auth_service import (
    _create_first_admin_locked,
    _lock_bootstrap_admin,
    utc_now,
)

SETUP_TOKEN_PREFIX = "apanel_setup_"
SetupStatus = Literal["NOT_READY", "PENDING", "COMPLETED"]


async def get_setup_status(session: AsyncSession) -> SetupStatus:
    """Expose only public installation state, never token material."""

    setup = await session.get(InstallationSetup, 1)
    if setup is not None:
        return "COMPLETED" if setup.completed_at is not None else "PENDING"
    # A database initialized before the setup job (or by a manual CLI admin)
    # must never show an unrestricted first-admin path.
    exists = (
        await session.execute(select(User.id).where(User.role == UserRole.ADMIN).limit(1))
    ).scalar_one_or_none()
    return "COMPLETED" if exists is not None else "NOT_READY"


async def complete_setup(
    session: AsyncSession, *, token: str, username: str, email: str, password: str
) -> User:
    """Atomically consume the token, create admin and permanently close setup."""

    async with session.begin():
        await _lock_bootstrap_admin(session)
        setup = (
            await session.execute(
                select(InstallationSetup).where(InstallationSetup.id == 1).with_for_update()
            )
        ).scalar_one_or_none()
        if setup is None:
            raise ApiError("SETUP_NOT_READY", "Installation setup is not ready.", 503)
        if setup.completed_at is not None:
            raise ApiError("SETUP_ALREADY_COMPLETED", "Installation is already complete.", 409)
        if not setup.token_hash or not hmac.compare_digest(
            hash_opaque_token(token), setup.token_hash
        ):
            raise ApiError("INVALID_SETUP_TOKEN", "Invalid setup token.", 403)

        # This helper also rejects legacy administrators. Both changes must
        # commit or roll back together, under the same PostgreSQL lock.
        user = await _create_first_admin_locked(
            session, username=username, email=email, password=password
        )
        setup.token_hash = None
        setup.completed_at = utc_now()
    return user


def _read_setup_token(path: Path) -> str | None:
    """Reject symlinks, insecure mode or ownership, and arbitrary file content."""

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("Cannot safely read the existing setup token file.") from exc
    try:
        info = os.fstat(fd)
        parent = path.parent.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != parent.st_uid
        ):
            raise RuntimeError("Setup token file must be a regular owner-only file (0600).")
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            fd = -1
            raw = stream.read(256)
    finally:
        if fd != -1:
            os.close(fd)
    token = raw.rstrip("\n")
    if not token.startswith(SETUP_TOKEN_PREFIX) or len(token) != len(SETUP_TOKEN_PREFIX) + 64:
        raise RuntimeError("The existing setup token file is invalid.")
    return token


def _write_setup_token(path: Path, token: str) -> None:
    """Atomically install a root-visible token owned by the host project owner."""

    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise RuntimeError("Setup token parent must be an existing directory.")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise RuntimeError("Refusing to replace a non-regular setup token file.")

    fd, temporary = tempfile.mkstemp(prefix=".apanel-setup-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        owner = parent.stat()
        if os.geteuid() == 0:
            os.fchown(fd, owner.st_uid, owner.st_gid)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            fd = -1
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if fd != -1:
            os.close(fd)
        if os.path.exists(temporary):
            os.unlink(temporary)


async def prepare_setup(
    session: AsyncSession, *, output: Path, rotate: bool = False
) -> Literal["CREATED", "REUSED", "ROTATED", "COMPLETED"]:
    """Idempotently issue a secret to the host only on an uninitialized DB.

    A missing/mismatched file with an existing pending DB token fails closed.
    A host operator may deliberately rotate it with --rotate.
    """

    async with session.begin():
        await _lock_bootstrap_admin(session)
        setup = (
            await session.execute(
                select(InstallationSetup).where(InstallationSetup.id == 1).with_for_update()
            )
        ).scalar_one_or_none()
        if setup is not None and setup.completed_at is not None:
            return "COMPLETED"

        existing_admin = (
            await session.execute(select(User.id).where(User.role == UserRole.ADMIN).limit(1))
        ).scalar_one_or_none()
        if existing_admin is not None:
            if setup is None:
                session.add(InstallationSetup(id=1, token_hash=None, completed_at=utc_now()))
            else:
                setup.token_hash = None
                setup.completed_at = utc_now()
            return "COMPLETED"

        if setup is not None and not rotate:
            token = _read_setup_token(output)
            if token is None or not setup.token_hash or not hmac.compare_digest(
                hash_opaque_token(token), setup.token_hash
            ):
                raise RuntimeError(
                    "Pending setup token file is missing or mismatched; "
                    "use the explicit --rotate recovery option."
                )
            return "REUSED"

        token = SETUP_TOKEN_PREFIX + generate_opaque_token()
        _write_setup_token(output, token)
        if setup is None:
            session.add(InstallationSetup(id=1, token_hash=hash_opaque_token(token)))
        else:
            setup.token_hash = hash_opaque_token(token)
        return "ROTATED" if rotate else "CREATED"
