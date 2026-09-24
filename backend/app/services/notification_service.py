"""Persist and deliver provider-neutral notification attempts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationChannel, NotificationStatus, UserSetting
from app.providers.notification import (
    FeishuProvider,
    NotificationMessage,
    NotificationProvider,
    NotificationProviderError,
)


class ProviderFactory(Protocol):
    """Build a channel provider for one user's destination."""

    def __call__(self, webhook_url: str | None) -> NotificationProvider: ...


class NotificationService:
    """Own provider calls and durable delivery outcomes.

    Alert evaluation only decides *when* to create a notification.  This
    service owns the provider boundary, user destination lookup, and safe
    ``PENDING``/``SENT``/``FAILED`` persistence.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: NotificationProvider | None = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.provider_factory = provider_factory or (lambda url: FeishuProvider(url))

    async def deliver(
        self,
        *,
        user_id: int,
        alert_rule_id: int,
        security_id: int,
        title: str,
        message: NotificationMessage,
        provider: NotificationProvider | None = None,
        created_at: datetime | None = None,
    ) -> Notification:
        """Record and deliver one alert notification.

        The durable row is committed before the network call, so a provider
        failure is represented as a diagnosable notification rather than
        rolling back the alert's edge claim.  Webhook plaintext never enters
        the returned notification or an exception message.
        """

        content = message.to_dict()
        content["title"] = title
        notification = Notification(
            user_id=user_id,
            alert_rule_id=alert_rule_id,
            security_id=security_id,
            channel=NotificationChannel.FEISHU,
            title=title,
            content=content,
            status=NotificationStatus.PENDING,
            created_at=created_at or datetime.now(UTC),
        )
        self.session.add(notification)
        await self.session.commit()

        webhook = await self._user_webhook(user_id)
        active_provider = provider if provider is not None else self.provider
        owns_provider = False
        try:
            if active_provider is None:
                active_provider = self.provider_factory(webhook)
                owns_provider = True
            await active_provider.send({"settings": {"feishu_webhook": webhook}}, message)
        except Exception as exc:
            code, safe_message = _safe_delivery_error(exc, webhook)
            notification.status = NotificationStatus.FAILED
            notification.error_code = code
            notification.error_message = safe_message
            notification.content = {
                **content,
                "error": {"code": code, "message": safe_message},
            }
        else:
            notification.status = NotificationStatus.SENT
            notification.sent_at = datetime.now(UTC)
        finally:
            if owns_provider and active_provider is not None:
                close = getattr(active_provider, "close", None)
                if close is not None:
                    try:
                        result = close()
                        if hasattr(result, "__await__"):
                            await result
                    except Exception:
                        # A close failure must not hide the already recorded
                        # provider outcome or leak transport diagnostics.
                        pass
        await self.session.commit()
        return notification

    async def summarize_deliveries(
        self,
        notification_ids: Sequence[int],
    ) -> dict[str, int]:
        """Return a verifiable delivery summary for one pipeline batch.

        Alert evaluation already persists and delivers the notification before
        returning its edge-trigger result.  The EOD pipeline's final stage
        therefore verifies those durable outcomes instead of sending the same
        notification a second time.
        """

        unique_ids = tuple(dict.fromkeys(int(item) for item in notification_ids))
        if not unique_ids:
            return {"total": 0, "sent": 0, "failed": 0, "pending": 0}
        rows = list(
            (
                await self.session.execute(
                    select(Notification.id, Notification.status).where(
                        Notification.id.in_(unique_ids)
                    )
                )
            ).all()
        )
        found_ids = {int(notification_id) for notification_id, _status in rows}
        if found_ids != set(unique_ids):
            raise ValueError("notification delivery record is missing")
        counts = Counter(str(status) for _notification_id, status in rows)
        return {
            "total": len(unique_ids),
            "sent": counts.get(NotificationStatus.SENT.value, 0),
            "failed": counts.get(NotificationStatus.FAILED.value, 0),
            "pending": counts.get(NotificationStatus.PENDING.value, 0),
        }

    async def _user_webhook(self, user_id: int) -> str | None:
        setting = (
            await self.session.execute(select(UserSetting).where(UserSetting.user_id == user_id))
        ).scalar_one_or_none()
        if setting is None or not isinstance(setting.settings, dict):
            return None
        nested = setting.settings.get("notification_settings")
        if not isinstance(nested, dict):
            nested = setting.settings.get("notification")
        webhook = nested.get("feishu_webhook") if isinstance(nested, dict) else None
        return webhook.strip() if isinstance(webhook, str) and webhook.strip() else None


async def summarize_deliveries(
    session: AsyncSession,
    notification_ids: Sequence[int],
) -> dict[str, int]:
    """Stable scheduler entry point for one notification batch summary."""

    return await NotificationService(session).summarize_deliveries(notification_ids)


def _safe_delivery_error(error: Exception, webhook: str | None) -> tuple[str, str]:
    """Translate provider failures into bounded, secret-free diagnostics."""

    status_code = getattr(error, "status_code", None)
    business_code = getattr(error, "business_code", None)
    if isinstance(status_code, int):
        code = f"HTTP_{status_code}"
    elif business_code is not None:
        code = f"FEISHU_{business_code}"
    elif error.__class__.__name__ == "FeishuWebhookError":
        code = "FEISHU_ERROR"
    elif isinstance(error, NotificationProviderError):
        code = "PROVIDER_ERROR"
    else:
        code = "PROVIDER_ERROR"
    # Concrete providers promise sanitized diagnostics.  Unknown provider
    # exceptions intentionally use a generic message to avoid leaking secrets.
    message = (
        str(error)
        if isinstance(error, NotificationProviderError)
        else "Notification provider failed."
    )
    if webhook:
        message = message.replace(webhook, "<redacted-webhook>")
    if len(message) > 255:
        message = message[:252] + "..."
    return code[:64], message


__all__ = ["NotificationService", "ProviderFactory", "summarize_deliveries"]
