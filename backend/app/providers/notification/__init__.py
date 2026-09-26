"""Notification provider interfaces, messages, and adapters."""

from .base import NotificationProvider
from .feishu import (
    FEISHU_WEBHOOK_TIMEOUT_SECONDS,
    FeishuNotificationProvider,
    FeishuProvider,
    FeishuWebhookError,
    FeishuWebhookProvider,
    NotificationProviderError,
)
from .message import NotificationMessage
from .registry import (
    DEFAULT_PROVIDER_REGISTRY,
    NotificationProviderRegistry,
    create_default_registry,
)

__all__ = [
    "FEISHU_WEBHOOK_TIMEOUT_SECONDS",
    "FeishuNotificationProvider",
    "FeishuProvider",
    "FeishuWebhookError",
    "FeishuWebhookProvider",
    "NotificationMessage",
    "NotificationProvider",
    "NotificationProviderError",
    "NotificationProviderRegistry",
    "DEFAULT_PROVIDER_REGISTRY",
    "create_default_registry",
]
