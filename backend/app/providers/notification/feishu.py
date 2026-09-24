"""Feishu webhook notification provider.

The adapter owns HTTP concerns only.  Rule matching and edge-trigger state
remain in :mod:`app.alerts`, and the provider never stores delivery state.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import NotificationProvider
from .message import NotificationMessage

logger = logging.getLogger(__name__)


class NotificationProviderError(RuntimeError):
    """A notification could not be delivered."""


class FeishuWebhookError(NotificationProviderError):
    """A Feishu webhook rejected or failed to process a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        business_code: str | int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.business_code = business_code


def _redacted_endpoint(url: str) -> str:
    """Return only a safe endpoint label; webhook tokens never reach logs."""

    try:
        parsed = urlsplit(url)
        if parsed.scheme and parsed.hostname:
            port = f":{parsed.port}" if parsed.port is not None else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}"
    except ValueError:
        pass
    return "<invalid-webhook>"


def _validate_webhook_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise FeishuWebhookError("Feishu webhook URL is required")
    normalized = url.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise FeishuWebhookError("Feishu webhook URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FeishuWebhookError("Feishu webhook URL is invalid")
    return normalized


def _message_text(message: NotificationMessage | str) -> str:
    if isinstance(message, NotificationMessage):
        return message.render_text()
    if not isinstance(message, str) or not message.strip():
        raise FeishuWebhookError("notification message is required")
    return message


def _response_code(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return None
    # Both spellings have appeared in Feishu webhook responses.
    return payload.get("code", payload.get("StatusCode"))


def _is_success_code(code: Any) -> bool:
    return code is None or str(code).strip() == "0"


class FeishuWebhookProvider(NotificationProvider):
    """Send text messages to a Feishu custom bot webhook.

    ``client``/``http_client`` is injectable and need only expose an async
    ``post`` method.  This keeps unit tests fully offline and also permits the
    application to share a configured ``httpx.AsyncClient``.  An injected
    client is never closed by this provider.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        *,
        client: Any | None = None,
        http_client: Any | None = None,
        timeout_seconds: float = 5.0,
        timeout: float | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        if client is not None and http_client is not None:
            raise ValueError("client and http_client cannot both be provided")
        resolved_timeout = timeout_seconds if timeout is None else timeout
        if isinstance(resolved_timeout, bool) or not isinstance(resolved_timeout, (int, float)):
            raise ValueError("timeout_seconds must be a positive number")
        if resolved_timeout <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        self.webhook_url = None if webhook_url is None else _validate_webhook_url(webhook_url)
        self.timeout_seconds = float(resolved_timeout)
        self._client = client if client is not None else http_client
        self._owns_client = self._client is None
        self._logger = logger_ or logger

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))
        return self._client

    def _resolve_webhook_url(self, user: Mapping[str, Any] | str | None) -> str:
        if isinstance(user, str):
            return _validate_webhook_url(user)
        if isinstance(user, Mapping):
            for key in ("feishu_webhook_url", "webhook_url", "notification_webhook"):
                candidate = user.get(key)
                if candidate is not None:
                    return _validate_webhook_url(candidate)
            settings = user.get("settings")
            if isinstance(settings, Mapping):
                for key in ("feishu_webhook_url", "webhook_url"):
                    candidate = settings.get(key)
                    if candidate is not None:
                        return _validate_webhook_url(candidate)
        if self.webhook_url is None:
            raise FeishuWebhookError("Feishu webhook URL is required")
        return self.webhook_url

    async def send(
        self,
        user: Mapping[str, Any] | str | NotificationMessage | None = None,
        message: NotificationMessage | str | None = None,
    ) -> None:
        """Send one text message, raising a safe provider error on failure."""

        # A configured provider can be called as ``send(message)`` while the
        # original provider contract remains ``send(user, message)``.
        if message is None:
            if isinstance(user, (NotificationMessage, str)):
                message = user
                user = None
            else:
                raise FeishuWebhookError("notification message is required")
        endpoint = self._resolve_webhook_url(user)
        text = _message_text(message)
        payload = {"msg_type": "text", "content": {"text": text}}
        endpoint_label = _redacted_endpoint(endpoint)
        try:
            response = await self._get_client().post(
                endpoint,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException):
            self._logger.warning("Feishu webhook timed out endpoint=%s", endpoint_label)
            raise FeishuWebhookError("Feishu webhook request timed out") from None
        except httpx.RequestError:
            self._logger.warning("Feishu webhook request failed endpoint=%s", endpoint_label)
            raise FeishuWebhookError("Feishu webhook request failed") from None
        except Exception:
            # Fake clients and alternative transports may expose a different
            # exception hierarchy; do not leak their URL-bearing messages.
            self._logger.warning("Feishu webhook request failed endpoint=%s", endpoint_label)
            raise FeishuWebhookError("Feishu webhook request failed") from None

        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            self._logger.warning(
                "Feishu webhook returned non-success status=%s endpoint=%s",
                status_code,
                endpoint_label,
            )
            raise FeishuWebhookError(
                f"Feishu webhook returned HTTP {status_code}", status_code=status_code
            )

        try:
            body = response.json()
            if inspect.isawaitable(body):
                body = await body
        except Exception:
            self._logger.warning("Feishu webhook returned invalid JSON endpoint=%s", endpoint_label)
            raise FeishuWebhookError("Feishu webhook returned an invalid response") from None
        business_code = _response_code(body)
        if not _is_success_code(business_code):
            self._logger.warning(
                "Feishu webhook rejected message code=%s endpoint=%s",
                business_code,
                endpoint_label,
            )
            raise FeishuWebhookError(
                "Feishu webhook rejected the message", business_code=business_code
            )

    async def close(self) -> None:
        """Close only the HTTP client owned by this provider."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> FeishuWebhookProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


# Both names make the adapter discoverable to callers using either the
# architecture document's provider name or the concrete webhook name.
FeishuProvider = FeishuWebhookProvider
FeishuNotificationProvider = FeishuWebhookProvider


__all__ = [
    "FeishuNotificationProvider",
    "FeishuProvider",
    "FeishuWebhookError",
    "FeishuWebhookProvider",
    "NotificationProviderError",
]
