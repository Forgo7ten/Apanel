"""Provider boundary for notification channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class NotificationProvider(ABC):
    """Async contract for Feishu and future notification adapters."""

    @abstractmethod
    async def send(self, user: Mapping[str, Any], message: str) -> None:
        """Send ``message`` to a user through the concrete provider."""
