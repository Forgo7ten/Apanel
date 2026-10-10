"""Public first-installation endpoints; token never enters a URL or response."""

from fastapi import APIRouter, Depends, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.session import get_db
from app.schemas.auth import UserData
from app.schemas.common import SuccessResponse
from app.schemas.setup import CompleteSetupData, CompleteSetupRequest, SetupStatusData
from app.services.installation_setup import complete_setup, get_setup_status

router = APIRouter()
_RATE_LIMIT_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""


async def limit_setup_attempts(request: Request) -> None:
    """Shared bounded guessing budget without trusting proxy IP headers."""
    try:
        count = await request.app.state.redis_client.eval(
            _RATE_LIMIT_SCRIPT, 1, "apanel:setup:attempts", 60
        )
    except (AttributeError, OSError, RedisError) as exc:
        raise ApiError("SETUP_UNAVAILABLE", "Setup temporarily unavailable.", 503) from exc
    if count > 30:
        raise ApiError("SETUP_RATE_LIMITED", "Too many setup attempts. Try again later.", 429)


@router.get("/status", response_model=SuccessResponse[SetupStatusData])
async def setup_status(
    response: Response, session: AsyncSession = Depends(get_db)  # noqa: B008
) -> SuccessResponse[SetupStatusData]:
    response.headers["Cache-Control"] = "no-store"
    return SuccessResponse(data=SetupStatusData(state=await get_setup_status(session)))


@router.post(
    "/complete",
    response_model=SuccessResponse[CompleteSetupData],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_setup_attempts)],
)
async def setup_complete(
    payload: CompleteSetupRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[CompleteSetupData]:
    response.headers["Cache-Control"] = "no-store"
    user = await complete_setup(
        session, token=payload.token, username=payload.username,
        email=payload.email, password=payload.password,
    )
    return SuccessResponse(data=CompleteSetupData(user=UserData.model_validate(user)))
