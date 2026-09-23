"""Celery application foundation for future scheduled jobs."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
settings.validate_database_credentials()

celery_app = Celery(
    "apanel",
    broker=settings.celery_broker_url or settings.redis_url,
    backend=settings.celery_result_backend or settings.redis_url,
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone=settings.timezone,
    enable_utc=False,
    beat_schedule={},
)
