"""Opaque keyset cursors for user-scoped notification history."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime

from app.core.errors import ApiError


def encode_notification_cursor(created_at: datetime, notification_id: int) -> str:
    # SQLite tests can return naive UTC timestamps; PostgreSQL returns aware UTC.
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    value = f"{created_at.isoformat()}|{notification_id}".encode()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_notification_cursor(value: str) -> tuple[datetime, int]:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        timestamp, separator, raw_id = raw.decode("utf-8").partition("|")
        created_at = datetime.fromisoformat(timestamp)
        notification_id = int(raw_id)
        if (
            not separator
            or notification_id <= 0
            or created_at.tzinfo is None
            or encode_notification_cursor(created_at, notification_id) != value
        ):
            raise ValueError("invalid cursor")
    except (ValueError, UnicodeError, binascii.Error, OverflowError) as exc:
        raise ApiError(
            "INVALID_NOTIFICATION_CURSOR", "Notification cursor is invalid.", 400
        ) from exc
    return created_at, notification_id
