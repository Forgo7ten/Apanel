"""Provider boundary for notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from .message import NotificationMessage


class NotificationProvider(ABC):
    """Async contract for Feishu and future notification adapters."""

    @abstractmethod
    async def send(
        self,
        user: Mapping[str, Any] | str | None,
        message: NotificationMessage | str,
    ) -> None:
        """Send a provider-neutral message to a user/destination.

        ``user`` remains a mapping for compatibility with the existing
        boundary.  Concrete providers may also accept a direct destination
        string or use a destination configured at construction time.
        """
