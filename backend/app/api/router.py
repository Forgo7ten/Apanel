"""Versioned API router."""

from fastapi import APIRouter

from app.api.health import router as health_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router, prefix="/health", tags=["health"])
