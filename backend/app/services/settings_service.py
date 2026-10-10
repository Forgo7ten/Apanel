"""Application service for per-user settings and encrypted notification secrets."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.indicators.parameters import canonicalize_parameters, normalize_indicator_type
from app.models import UserSetting
from app.repositories.settings import UserSettingsRepository
from app.schemas.settings import UserSettingsData
from app.security.webhook_url import validate_feishu_webhook_url
from app.services.user_secret_service import UserSecretService

_ALLOWED_ADJUSTMENTS = {"qfq", "none"}
_ALLOWED_DENSITY = {"compact", "comfortable"}
_WEBHOOK_MISSING = object()


class UserSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserSettingsRepository(session)
        self.secret_service = UserSecretService(session)

    async def get(self, user_id: int) -> UserSettingsData:
        setting = await self.repository.get_for_user(user_id)
        settings = _public_settings(setting.settings if setting is not None else {})
        settings.setdefault("notification_settings", {})[
            "feishu_webhook_configured"
        ] = await self.secret_service.configured(user_id)
        return UserSettingsData(
            settings=settings,
            created_at=setting.created_at if setting is not None else None,
            updated_at=setting.updated_at if setting is not None else None,
        )

    async def update(self, user_id: int, updates: dict[str, Any]) -> UserSettingsData:
        normalized_updates, webhook = _normalize_updates(updates)
        try:
            await self.secret_service.lock_owner(user_id)
            setting = await self.repository.get_for_user(user_id)
            existing = _public_settings(setting.settings if setting is not None else {})
            if webhook is not _WEBHOOK_MISSING:
                if webhook is None:
                    await self.secret_service.clear_feishu_webhook(user_id)
                else:
                    await self.secret_service.set_feishu_webhook(user_id, webhook)
            merged = _merge_settings(existing, normalized_updates)
            if setting is None:
                setting = UserSetting(user_id=user_id, settings=merged)
                self.session.add(setting)
            else:
                setting.settings = merged
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("SETTINGS_UPDATE_FAILED", "Settings could not be updated.", 409) from exc
        except BaseException:
            await self.session.rollback()
            raise
        await self.session.refresh(setting)
        return await self.get(user_id)


def _merge_settings(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _normalize_updates(updates: dict[str, Any]) -> tuple[dict[str, Any], object]:
    normalized = dict(updates)
    if any(key in normalized for key in ("settings", "notification", "feishu_webhook")):
        raise ApiError("INVALID_SETTINGS", "Use the current typed settings fields.", 400)
    if "adjust_type" in normalized:
        adjustment = str(normalized["adjust_type"]).strip().lower()
        if adjustment not in _ALLOWED_ADJUSTMENTS:
            raise ApiError("INVALID_SETTINGS", "adjust_type must be qfq or none.", 400)
        normalized["adjust_type"] = adjustment

    indicator_settings = normalized.get("indicator_settings")
    if isinstance(indicator_settings, dict):
        indicator_settings = dict(indicator_settings)
        defaults = indicator_settings.get("defaults")
        if defaults is not None:
            if not isinstance(defaults, list):
                raise ApiError("INVALID_SETTINGS", "indicator defaults must be a list.", 400)
            indicator_settings["defaults"] = [normalize_indicator_type(item) for item in defaults]
        parameters = indicator_settings.get("parameters")
        if isinstance(parameters, dict):
            canonical: dict[str, Any] = {}
            for indicator, raw in parameters.items():
                normalized_type = normalize_indicator_type(indicator)
                if normalized_type == "DIVIDEND_YIELD":
                    canonical[normalized_type] = {}
                else:
                    if not isinstance(raw, dict):
                        raise ApiError(
                            "INVALID_SETTINGS", "Indicator parameters must be an object.", 400
                        )
                    canonical[normalized_type] = canonicalize_parameters(
                        normalized_type, raw, fill_defaults=True
                    )
            indicator_settings["parameters"] = canonical
        normalized["indicator_settings"] = indicator_settings

    display = normalized.get("display_settings")
    if isinstance(display, dict):
        display = dict(display)
        if "density" in display and display["density"] not in _ALLOWED_DENSITY:
            raise ApiError("INVALID_SETTINGS", "display density is invalid.", 400)
        for key in ("show_states", "show_deltas", "show_mini_chart"):
            if key in display and not isinstance(display[key], bool):
                raise ApiError("INVALID_SETTINGS", f"{key} must be boolean.", 400)
        normalized["display_settings"] = display

    notification = normalized.get("notification_settings")
    webhook: object = _WEBHOOK_MISSING
    if notification is not None:
        if not isinstance(notification, dict):
            raise ApiError("INVALID_SETTINGS", "Notification settings must be an object.", 400)
        notification = dict(notification)
        if "feishu_webhook_configured" in notification:
            raise ApiError("INVALID_SETTINGS", "Webhook configuration status is read-only.", 400)
        supplied = notification.pop("feishu_webhook", _WEBHOOK_MISSING)
        if supplied is not _WEBHOOK_MISSING:
            webhook = supplied
        allowed = get_settings().feishu_webhook_allowed_hosts.split(",")
        if webhook is not _WEBHOOK_MISSING:
            try:
                webhook = (
                    None
                    if webhook is None
                    else validate_feishu_webhook_url(webhook, allowed_hosts=allowed)
                )
            except ValueError as exc:
                raise ApiError(
                    "INVALID_WEBHOOK_URL", "Feishu webhook URL is invalid.", 400
                ) from exc
        normalized["notification_settings"] = notification
    return normalized, webhook


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    # Defense in depth: never expose or re-persist a raw credential even when
    # an administrative/database writer produced an invalid JSON payload.
    public = dict(settings)
    public.pop("feishu_webhook", None)
    public.pop("notification", None)
    nested = public.get("notification_settings")
    if not isinstance(nested, dict):
        nested = {}
    nested = dict(nested)
    nested.pop("feishu_webhook", None)
    nested.pop("feishu_webhook_configured", None)
    public["notification_settings"] = nested
    return public


__all__ = ["UserSettingsService"]
