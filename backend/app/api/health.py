"""Backend health endpoint."""

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorResponse
from app.schemas.health import HealthResponse
from app.services.health_service import HealthService

router = APIRouter()


def get_health_service(request: Request) -> HealthService:
    """Resolve the app-scoped health service; replaceable in unit tests."""

    return request.app.state.health_service


@router.get(
    "",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
    summary="Check backend and infrastructure health",
)
async def health_check(
    health_service: HealthService = Depends(get_health_service),  # noqa: B008
) -> JSONResponse:
    """Return PostgreSQL and Redis status with a conventional 503 on failure."""

    data = await health_service.check()
    healthy = data.status == "healthy"
    response = HealthResponse(
        success=healthy,
        data=data,
        error=None
        if healthy
        else ErrorResponse(
            code="DEPENDENCY_UNAVAILABLE",
            message="One or more infrastructure dependencies are unavailable.",
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=response.model_dump(mode="json"),
    )
