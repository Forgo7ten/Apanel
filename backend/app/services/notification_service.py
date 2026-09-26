"""Persist and deliver provider-neutral notification attempts."""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ApiError
from app.models import Notification, NotificationChannel, NotificationStatus, UserSetting
from app.providers.notification import (
    DEFAULT_PROVIDER_REGISTRY,
    FEISHU_WEBHOOK_TIMEOUT_SECONDS,
    NotificationMessage,
    NotificationProvider,
    NotificationProviderError,
    NotificationProviderRegistry,
)


class ProviderFactory(Protocol):
    """Build a channel provider for one user's destination."""

    def __call__(self, webhook_url: str | None) -> NotificationProvider: ...


# A PENDING row older than this window is treated as an abandoned/legacy
# attempt.  Recent PENDING rows remain owned by the original delivery path;
# retrying them would risk a second provider POST.
NOTIFICATION_PROVIDER_TIMEOUT = timedelta(seconds=FEISHU_WEBHOOK_TIMEOUT_SECONDS)
NOTIFICATION_PENDING_RECOVERY_MARGIN = timedelta(seconds=5)
NOTIFICATION_PENDING_RECOVERY_WINDOW = timedelta(minutes=5)
NOTIFICATION_COMMIT_RECOVERY_ATTEMPTS = 2
NOTIFICATION_PERSISTENCE_ERROR_CODE = "NOTIFICATION_PERSISTENCE_FAILED"
NOTIFICATION_PERSISTENCE_ERROR_MESSAGE = "Notification delivery outcome could not be saved."
_RETRY_LOCKS_GUARD = threading.Lock()
_RETRY_LOCKS_BY_LOOP: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[int, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _retry_lock(notification_id: int) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _RETRY_LOCKS_GUARD:
        locks = _RETRY_LOCKS_BY_LOOP.setdefault(loop, {})
        return locks.setdefault(notification_id, asyncio.Lock())


def validate_pending_recovery_window(value: timedelta) -> timedelta:
    """Require recovery to wait beyond provider timeout and DB settling time."""

    minimum = NOTIFICATION_PROVIDER_TIMEOUT + NOTIFICATION_PENDING_RECOVERY_MARGIN
    if not isinstance(value, timedelta) or value <= minimum:
        raise ValueError("pending recovery window must exceed provider timeout and DB margin")
    return value


validate_pending_recovery_window(NOTIFICATION_PENDING_RECOVERY_WINDOW)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_notification_retryable(
    notification: Notification,
    *,
    now: datetime | None = None,
    recovery_window: timedelta = NOTIFICATION_PENDING_RECOVERY_WINDOW,
) -> bool:
    """Return whether an authenticated caller may retry this row."""

    validate_pending_recovery_window(recovery_window)
    if notification.status == NotificationStatus.FAILED:
        return True
    if notification.status != NotificationStatus.PENDING:
        return False
    current = _utc_datetime(now or datetime.now(UTC))
    created_at = _utc_datetime(notification.created_at)
    return current >= created_at + recovery_window


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
        pending_recovery_window: timedelta = NOTIFICATION_PENDING_RECOVERY_WINDOW,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.provider_factory = provider_factory
        self.provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self.pending_recovery_window = validate_pending_recovery_window(pending_recovery_window)
        self.clock = clock or (lambda: datetime.now(UTC))

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

        return await self._deliver_new(notification.id, message, provider=provider)

    async def retry(
        self,
        *,
        user_id: int,
        notification_id: int,
        provider: NotificationProvider | None = None,
    ) -> Notification:
        """Retry one recoverable delivery without re-evaluating its alert edge.

        FAILED rows are an explicit retry boundary.  An old PENDING row is
        recoverable because its original worker may have disappeared before
        recording a terminal result.  The row lock and process-local lock
        cover the state transition and provider call, so concurrent retries
        cannot issue duplicate POSTs for a successful attempt.
        """
        async with _retry_lock(notification_id):
            notification = await self._locked_notification(
                notification_id,
                user_id=user_id,
            )
            if notification is None:
                raise ApiError("NOTIFICATION_NOT_FOUND", "Notification was not found.", 404)
            if notification.status == NotificationStatus.SENT:
                return notification
            if notification.status == NotificationStatus.PENDING and not is_notification_retryable(
                notification,
                now=self.clock(),
                recovery_window=self.pending_recovery_window,
            ):
                raise ApiError(
                    "NOTIFICATION_RETRY_CONFLICT",
                    "Notification delivery is still in progress.",
                    409,
                )
            if notification.status not in {
                NotificationStatus.FAILED,
                NotificationStatus.PENDING,
            }:
                raise ApiError(
                    "NOTIFICATION_NOT_RETRYABLE",
                    "Notification cannot be retried in its current state.",
                    409,
                )

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
            # Keep the row lock until _deliver_existing commits a terminal
            # result.  Committing PENDING here would reopen the duplicate-POST
            # race that this recovery path is meant to close.
            return await self._deliver_existing(notification, message, provider=provider)

    async def _deliver_new(
        self,
        notification_id: int,
        message: NotificationMessage,
        *,
        provider: NotificationProvider | None,
    ) -> Notification:
        """Deliver a newly committed row through the same lock as recovery."""

        async with _retry_lock(notification_id):
            notification = await self._locked_notification(notification_id)
            if notification is None:
                raise RuntimeError("notification disappeared before delivery")
            # A stale retry may have acquired the row first after the initial
            # PENDING commit.  Its terminal result wins; never POST twice.
            if notification.status != NotificationStatus.PENDING:
                return notification
            return await self._deliver_existing(notification, message, provider=provider)

    async def _locked_notification(
        self,
        notification_id: int,
        *,
        user_id: int | None = None,
    ) -> Notification | None:
        """Load one notification while holding its database row lock."""

        conditions = [Notification.id == notification_id]
        if user_id is not None:
            conditions.append(Notification.user_id == user_id)
        return (
            await self.session.execute(
                select(Notification)
                .options(selectinload(Notification.security))
                .where(*conditions)
                .with_for_update()
            )
        ).scalar_one_or_none()

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
        """Commit a terminal outcome without overwriting a concurrent winner."""

        notification_id = notification.id
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
            # A commit exception is ambiguous: the provider result may have
            # been committed before the client observed the exception.  Drop
            # the failed transaction, then re-lock and inspect the durable
            # row before applying the cached outcome.
            await self._rollback_quietly()
            for _ in range(NOTIFICATION_COMMIT_RECOVERY_ATTEMPTS):
                try:
                    persisted = await self._locked_notification(notification_id)
                    if persisted is None:
                        raise RuntimeError("notification disappeared during outcome recovery")
                    if persisted.status in {
                        NotificationStatus.SENT,
                        NotificationStatus.FAILED,
                    }:
                        return persisted
                    if persisted.status != NotificationStatus.PENDING:
                        raise RuntimeError("notification has an invalid delivery status")
                    result = await self.session.execute(
                        update(Notification)
                        .where(
                            Notification.id == notification_id,
                            Notification.status == NotificationStatus.PENDING,
                        )
                        .values(**outcome)
                    )
                    if result.rowcount != 1:
                        await self._rollback_quietly()
                        continue
                    await self.session.commit()
                    for field, value in outcome.items():
                        setattr(persisted, field, value)
                    return persisted
                except Exception:
                    await self._rollback_quietly()
            raise ApiError(
                NOTIFICATION_PERSISTENCE_ERROR_CODE,
                NOTIFICATION_PERSISTENCE_ERROR_MESSAGE,
                503,
            ) from None
        return notification

    async def _rollback_quietly(self) -> None:
        try:
            await self.session.rollback()
        except Exception:
            pass

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


def _safe_delivery_error(error: Exception, _webhook: str | None) -> tuple[str, str]:
    """Translate provider failures into bounded, secret-free diagnostics."""

    status_code = getattr(error, "status_code", None)
    business_code = getattr(error, "business_code", None)
    if type(status_code) is int:
        code = f"HTTP_{status_code}"
    elif type(business_code) is int:
        code = f"FEISHU_{business_code}"
    elif error.__class__.__name__ == "FeishuWebhookError":
        code = "FEISHU_ERROR"
    elif isinstance(error, NotificationProviderError):
        code = "PROVIDER_ERROR"
    else:
        code = "PROVIDER_ERROR"
    # Concrete providers promise sanitized diagnostics.  Unknown provider
    # exceptions intentionally use a generic message to avoid leaking secrets.
    # Provider exceptions are an internal diagnostic boundary.  Persist only
    # fixed messages; even a custom provider must not be able to echo a URL,
    # token, or transport exception into API-visible history.
    return code[:64], "Notification provider failed."


__all__ = [
    "NOTIFICATION_COMMIT_RECOVERY_ATTEMPTS",
    "NOTIFICATION_PENDING_RECOVERY_WINDOW",
    "NOTIFICATION_PENDING_RECOVERY_MARGIN",
    "NOTIFICATION_PERSISTENCE_ERROR_CODE",
    "NOTIFICATION_PERSISTENCE_ERROR_MESSAGE",
    "NOTIFICATION_PROVIDER_TIMEOUT",
    "NotificationService",
    "ProviderFactory",
    "is_notification_retryable",
    "summarize_deliveries",
    "validate_pending_recovery_window",
]
