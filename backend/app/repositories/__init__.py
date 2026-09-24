"""Persistence-facing repository boundaries."""

from .alert import AlertRepository
from .indicator_state import IndicatorStateRepository
from .notification import NotificationRepository
from .security import DIVIDEND_EVENTS_TABLE, DividendEventRecord, SecurityRepository
from .settings import UserSettingsRepository
from .watch_table import WatchTableRepository

__all__ = [
    "AlertRepository",
    "IndicatorStateRepository",
    "NotificationRepository",
    "DIVIDEND_EVENTS_TABLE",
    "DividendEventRecord",
    "SecurityRepository",
    "UserSettingsRepository",
    "WatchTableRepository",
]
