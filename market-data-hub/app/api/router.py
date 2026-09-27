"""Market data service API router."""

from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.internal_market_data import router as internal_market_data_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(internal_market_data_router)
