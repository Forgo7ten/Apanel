"""Celery Beat schedules for Shanghai trading hours."""

from __future__ import annotations

from typing import Any

from celery.schedules import crontab


def build_beat_schedule(settings: Any) -> dict[str, dict[str, Any]]:
    """Build schedules from settings so deployments can override env values.

    Quotes run every 5--10 minutes during the two A-share intraday sessions.
    The EOD job runs after the 15:00 close, once the market-data provider has
    had time to publish a stable completed-day bar.
    """

    refresh_minutes = int(settings.quote_refresh_interval_minutes)
    return {
        "refresh-intraday-quotes": {
            "task": "apanel.tasks.refresh_quotes",
            "schedule": crontab(
                minute=f"*/{refresh_minutes}",
                hour="9-11,13-14",
                day_of_week="mon-fri",
            ),
            "options": {"queue": "market-data"},
        },
        "run-end-of-day-pipeline": {
            "task": "apanel.tasks.run_eod_pipeline",
            "schedule": crontab(
                minute=int(settings.eod_pipeline_minute),
                hour=int(settings.eod_pipeline_hour),
                day_of_week="mon-fri",
            ),
            "options": {"queue": "pipeline"},
        },
    }


__all__ = ["build_beat_schedule"]
