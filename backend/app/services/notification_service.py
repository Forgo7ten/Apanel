"""Persist and deliver provider-neutral notification attempts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationChannel, NotificationStatus, UserSetting
from app.providers.notification import (
    DEFAULT_PROVIDER_REGISTRY,
    NotificationMessage,
    NotificationProvider,
    NotificationProviderError,
    NotificationProviderRegistry,
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
        provider_registry: NotificationProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.provider_factory = provider_factory
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY

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

        return await self._deliver_existing(notification, message, provider=provider)

    async def retry(
        self,
        *,
        user_id: int,
        notification_id: int,
        provider: NotificationProvider | None = None,
    ) -> Notification:
        """Retry one failed delivery without re-evaluating its alert edge.

        A failed notification is an explicit retry boundary.  The alert
        instance remains ACTIVE, so normal evaluations cannot duplicate a
        successful delivery; a caller can retry the durable FAILED row when
        the provider becomes healthy.  SENT rows are idempotent no-ops.
        """

        notification = (
            await self.session.execute(
                select(Notification).where(
                    Notification.id == notification_id,
                    Notification.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if notification is None:
            raise LookupError("notification was not found")
        if notification.status == NotificationStatus.SENT:
            return notification
        if notification.status != NotificationStatus.FAILED:
            raise ValueError("only failed notifications can be retried")

        try:
            message = _message_from_content(notification.content)
        except Exception:
            # Keep malformed historical rows diagnosable and never turn a
            # retry request into an untracked PENDING attempt.
            notification.status = NotificationStatus.FAILED
            notification.error_code = "INVALID_NOTIFICATION_CONTENT"
            notification.error_message = "Notification content cannot be retried."
            await self.session.commit()
            return notification

        content = message.to_dict()
        content["title"] = notification.title
        notification.status = NotificationStatus.PENDING
        notification.error_code = None
        notification.error_message = None
        notification.content = content
        await self.session.commit()
        return await self._deliver_existing(notification, message, provider=provider)

    async def _deliver_existing(
        self,
        notification: Notification,
        message: NotificationMessage,
        *,
        provider: NotificationProvider | None,
    ) -> Notification:
        """Send an already committed row and persist a terminal outcome."""

        webhook: str | None = None
        active_provider = provider if provider is not None else self.provider
        owns_provider = False
        content = dict(notification.content or {})
        try:
            # The destination lookup is part of the provider boundary.  It
            # must be covered by the same failure handling as provider.send so
            # an exception can never leave this row PENDING.
            webhook = await self._user_webhook(notification.user_id)
            if active_provider is None:
                if self.provider_factory is not None:
                    active_provider = self.provider_factory(webhook)
                else:
                    active_provider = self.provider_registry.create(
                        NotificationChannel.FEISHU,
                        webhook,
                    )
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
            notification.error_code = None
            notification.error_message = None
            notification.sent_at = datetime.now(UTC)
            notification.content = content
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
        return await self._commit_delivery(notification)

    async def _commit_delivery(self, notification: Notification) -> Notification:
        """Commit a terminal outcome, recovering from a failed lookup txn."""

        outcome = {
            "status": notification.status,
            "error_code": notification.error_code,
            "error_message": notification.error_message,
            "sent_at": notification.sent_at,
            "content": dict(notification.content or {}),
        }
        try:
            await self.session.commit()
        except Exception:
            # A destination query can leave SQLAlchemy's transaction in a
            # failed state.  Roll it back, reload the already committed row,
            # and persist the terminal FAILED/SENT outcome again.
            await self.session.rollback()
            persisted = await self.session.get(Notification, notification.id)
            if persisted is None:
                raise
            for field, value in outcome.items():
                setattr(persisted, field, value)
            await self.session.commit()
            return persisted
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


def _message_from_content(content: object) -> NotificationMessage:
    if not isinstance(content, dict):
        raise ValueError("notification content must be an object")
    raw_date = content.get("date")
    if not isinstance(raw_date, str):
        raise ValueError("notification date is missing")
    return NotificationMessage(
        stock_name=content["stock_name"],
        stock_code=content["stock_code"],
        indicator=content["indicator"],
        state=content.get("state"),
        current_value=content.get("current_value"),
        previous_value=content.get("previous_value"),
        change=content.get("change"),
        date=date.fromisoformat(raw_date),
    )


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
