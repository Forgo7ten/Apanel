"""Versioned API router."""

from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.indicator_state import router as indicator_state_router
from app.api.securities import router as securities_router
from app.api.settings import router as settings_router
from app.api.watch_tables import router as watch_tables_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(indicator_state_router, tags=["indicators", "states"])
api_router.include_router(securities_router, tags=["securities", "market-data"])
api_router.include_router(watch_tables_router, tags=["watch-tables"])
api_router.include_router(settings_router, tags=["settings"])
