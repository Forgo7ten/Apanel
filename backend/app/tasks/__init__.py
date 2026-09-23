"""Celery worker and scheduler entry points."""

from .celery_app import celery_app

__all__ = ["celery_app"]
