"""Authentication HTTP controllers.

Controllers only translate HTTP input/output. Account and token business rules
live in :mod:`app.services.auth_service`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin
from app.core.config import Settings
from app.db.session import get_db
from app.models import User
from app.schemas.auth import (
    InvitationCreateRequest,
    InvitationData,
    LoginRequest,
    LogoutData,
    RegisterRequest,
    TokenData,
    UserData,
)
from app.schemas.common import SuccessResponse
from app.services.auth_service import (
    authenticate_and_issue_tokens,
    create_invitation,
    refresh_tokens,
    register_user,
)
from app.services.auth_service import (
    logout as logout_session,
)

router = APIRouter()
_REFRESH_COOKIE_PATH = "/api/v1/auth"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/invitations",
    response_model=SuccessResponse[InvitationData],
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation_endpoint(
    payload: InvitationCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    admin: User = Depends(require_admin),  # noqa: B008
) -> SuccessResponse[InvitationData]:
    result = await create_invitation(session, payload, admin=admin, settings=_settings(request))
    data = InvitationData(
        id=result.invitation.id,
        email=result.invitation.email,
        token=result.raw_token,
        status=result.invitation.status,
        expires_at=result.invitation.expires_at,
    )
    return SuccessResponse(data=data)


@router.post(
    "/register",
    response_model=SuccessResponse[UserData],
    status_code=status.HTTP_201_CREATED,
)
async def register_endpoint(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[UserData]:
    user = await register_user(session, payload)
    return SuccessResponse(data=UserData.model_validate(user))


@router.post("/login", response_model=SuccessResponse[TokenData])
async def login_endpoint(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[TokenData]:
    tokens = await authenticate_and_issue_tokens(session, payload, settings=_settings(request))
    _set_refresh_cookie(response, tokens.refresh_token, _settings(request))
    return SuccessResponse(
        data=TokenData(
            access_token=tokens.access_token,
            user=UserData.model_validate(tokens.user),
        )
    )


@router.post("/refresh", response_model=SuccessResponse[TokenData])
async def refresh_endpoint(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[TokenData]:
    settings = _settings(request)
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    tokens = await refresh_tokens(session, raw_token or "", settings=settings)
    _set_refresh_cookie(response, tokens.refresh_token, settings)
    return SuccessResponse(
        data=TokenData(
            access_token=tokens.access_token,
            user=UserData.model_validate(tokens.user),
        )
    )


@router.post("/logout", response_model=SuccessResponse[LogoutData])
async def logout_endpoint(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[LogoutData]:
    settings = _settings(request)
    await logout_session(session, request.cookies.get(settings.refresh_cookie_name))
    _delete_refresh_cookie(response, settings)
    return SuccessResponse(data=LogoutData())


@router.get("/me", response_model=SuccessResponse[UserData])
async def me_endpoint(
    user: User = Depends(get_current_user),  # noqa: B008
) -> SuccessResponse[UserData]:
    return SuccessResponse(data=UserData.model_validate(user))


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    secure = _refresh_cookie_secure(settings)
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        expires=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


def _delete_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        secure=_refresh_cookie_secure(settings),
        httponly=True,
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


def _refresh_cookie_secure(settings: Settings) -> bool:
    """Resolve one Secure policy for both setting and deleting the cookie."""

    if settings.refresh_cookie_secure is not None:
        return settings.refresh_cookie_secure
    return settings.app_env.casefold() == "production"
