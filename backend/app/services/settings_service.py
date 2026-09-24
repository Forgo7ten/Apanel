"""Application service for per-user settings."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import UserSetting
from app.repositories.settings import UserSettingsRepository
from app.schemas.settings import UserSettingsData


class UserSettingsService:
    """Read and merge settings only within the current user's scope."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserSettingsRepository(session)

    async def get(self, user_id: int) -> UserSettingsData:
        setting = await self.repository.get_for_user(user_id)
        if setting is None:
            return UserSettingsData()
        return _settings_data(setting)

    async def update(self, user_id: int, updates: dict[str, Any]) -> UserSettingsData:
        normalized_updates = _normalize_updates(updates)
        setting = await self.repository.get_for_user(user_id)
        if setting is None:
            setting = UserSetting(user_id=user_id, settings=normalized_updates)
            self.session.add(setting)
        else:
            setting.settings = _merge_settings(setting.settings, normalized_updates)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("SETTINGS_UPDATE_FAILED", "Settings could not be updated.", 409) from exc
        await self.session.refresh(setting)
        return _settings_data(setting)


def _settings_data(setting: UserSetting) -> UserSettingsData:
    return UserSettingsData(
        settings=_public_settings(setting.settings),
        created_at=setting.created_at,
        updated_at=setting.updated_at,
    )


def _merge_settings(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge known nested settings without replacing unrelated preferences."""

    merged = dict(existing)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _normalize_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize webhook updates without retaining plaintext markers."""

    normalized = dict(updates)
    nested = normalized.get("notification_settings")
    if not isinstance(nested, dict):
        nested = normalized.get("notification")
    if isinstance(nested, dict):
        nested = dict(nested)
        if "feishu_webhook" in nested:
            webhook = nested.pop("feishu_webhook")
            if webhook is None or (isinstance(webhook, str) and not webhook.strip()):
                # ``None`` is an explicit clear operation.  The service removes
                # any previously stored secret in ``_merge_settings`` below.
                nested["feishu_webhook"] = None
            else:
                nested["feishu_webhook"] = _validate_webhook(webhook)
        normalized["notification_settings"] = nested
        normalized.pop("notification", None)
    if "feishu_webhook" in normalized:
        webhook = normalized.pop("feishu_webhook")
        if webhook is None or (isinstance(webhook, str) and not webhook.strip()):
            normalized.setdefault("notification_settings", {})["feishu_webhook"] = None
        else:
            normalized.setdefault("notification_settings", {})[
                "feishu_webhook"
            ] = _validate_webhook(webhook)
    return normalized


def _validate_webhook(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError("INVALID_WEBHOOK_URL", "Feishu webhook URL is invalid.", 400)
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        raise ApiError("INVALID_WEBHOOK_URL", "Feishu webhook URL is invalid.", 400) from None
    if parsed.scheme != "https" or not parsed.hostname:
        raise ApiError("INVALID_WEBHOOK_URL", "Feishu webhook URL is invalid.", 400)
    return candidate


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return settings safe for an API response; never return webhook plaintext."""

    public = dict(settings)
    nested = public.get("notification_settings")
    if not isinstance(nested, dict):
        nested = public.get("notification")
    if isinstance(nested, dict):
        safe_nested = dict(nested)
        configured = bool(safe_nested.pop("feishu_webhook", None))
        # A persisted marker is only advisory; derive it from the secret so a
        # stale client-provided boolean cannot claim a configured destination.
        safe_nested["feishu_webhook_configured"] = configured
        public["notification_settings"] = safe_nested
        public.pop("notification", None)
    # Older callers may have sent a flat key.  Remove it even if no nested
    # notification object exists.
    public.pop("feishu_webhook", None)
    return public


__all__ = ["UserSettingsService"]
