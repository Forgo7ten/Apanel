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
    quote_task = {"task": "apanel.tasks.refresh_quotes", "options": {"queue": "market-data"}}
    return {
        "refresh-quotes-open": {
            **quote_task,
            "schedule": crontab(minute=f"30-55/{refresh_minutes}", hour="9", day_of_week="mon-fri"),
        },
        "refresh-quotes-morning": {
            **quote_task,
            "schedule": crontab(minute=f"*/{refresh_minutes}", hour="10", day_of_week="mon-fri"),
        },
        "refresh-quotes-before-lunch": {
            **quote_task,
            "schedule": crontab(minute=f"0-30/{refresh_minutes}", hour="11", day_of_week="mon-fri"),
        },
        "refresh-quotes-afternoon": {
            **quote_task,
            "schedule": crontab(minute=f"*/{refresh_minutes}", hour="13-14", day_of_week="mon-fri"),
        },
        "refresh-quotes-close": {
            **quote_task,
            "schedule": crontab(minute="0", hour="15", day_of_week="mon-fri"),
        },
        "dispatch-pending-notifications": {
            "task": "apanel.tasks.dispatch_pending_notifications",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "default"},
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
