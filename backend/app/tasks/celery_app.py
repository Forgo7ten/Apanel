"""Shared Celery application used by both worker and Beat processes."""

from celery import Celery
from kombu import Queue

from app.core.config import get_settings

from .schedule import build_beat_schedule

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
    beat_schedule=build_beat_schedule(settings),
    task_default_queue="default",
    task_queues=(
        Queue("default"),
        Queue("market-data"),
        Queue("pipeline"),
    ),
    task_routes={
        "apanel.tasks.refresh_quotes": {"queue": "market-data"},
        "apanel.tasks.run_eod_pipeline": {"queue": "pipeline"},
    },
    include=["app.tasks.jobs"],
)
