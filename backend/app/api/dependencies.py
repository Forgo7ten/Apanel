"""Shared HTTP dependencies for authentication and authorization."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import InvalidAccessToken, decode_access_token
from app.db.session import get_db
from app.models import User, UserRole
from app.services.auth_service import get_active_user

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> User:
    """Decode the bearer token and re-read active account state from PostgreSQL."""

    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise ApiError("AUTHENTICATION_REQUIRED", "Authentication is required.", 401)
    try:
        claims = decode_access_token(credentials.credentials, request.app.state.settings)
        user_id = int(claims["sub"])
    except (InvalidAccessToken, ValueError):
        raise ApiError("INVALID_ACCESS_TOKEN", "Access token is invalid or expired.", 401) from None
    return await get_active_user(session, user_id)


async def require_admin(user: User = Depends(get_current_user)) -> User:  # noqa: B008
    """Authorize by the current database role, never by a JWT role claim."""

    if user.role != UserRole.ADMIN:
        raise ApiError("FORBIDDEN", "Administrator access is required.", 403)
    return user
