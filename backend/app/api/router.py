"""Versioned API router."""

from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.indicator_state import router as indicator_state_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(indicator_state_router, tags=["indicators", "states"])
