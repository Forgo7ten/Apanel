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
        legacy_configured = (
            get_settings().legacy_webhook_fallback_enabled
            and _legacy_webhook(setting.settings if setting is not None else {}) is not None
        )
        settings.setdefault("notification_settings", {})["feishu_webhook_configured"] = (
            await self.secret_service.configured(user_id) or legacy_configured
        )
        return UserSettingsData(
            settings=settings,
            created_at=setting.created_at if setting is not None else None,
            updated_at=setting.updated_at if setting is not None else None,
        )

    async def update(self, user_id: int, updates: dict[str, Any]) -> UserSettingsData:
        normalized_updates, webhook = _normalize_updates(updates)
        setting = await self.repository.get_for_user(user_id)
        if setting is None:
            setting = UserSetting(user_id=user_id, settings=normalized_updates)
            self.session.add(setting)
        else:
            setting.settings = _merge_settings(
                _strip_legacy_webhook(setting.settings), normalized_updates
            )
        try:
            if webhook is not _WEBHOOK_MISSING:
                if webhook is None:
                    await self.secret_service.clear_feishu_webhook(user_id)
                else:
                    await self.secret_service.set_feishu_webhook(user_id, webhook)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("SETTINGS_UPDATE_FAILED", "Settings could not be updated.", 409) from exc
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
                    canonical[normalized_type] = canonicalize_parameters(
                        normalized_type,
                        _legacy_parameter_aliases(normalized_type, raw),
                        fill_defaults=True,
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

    webhook: object = _WEBHOOK_MISSING
    nested = normalized.get("notification_settings")
    if not isinstance(nested, dict):
        nested = normalized.pop("notification", None)
    if isinstance(nested, dict):
        nested = dict(nested)
        if "feishu_webhook" in nested:
            raw = nested.pop("feishu_webhook")
            if raw is None:
                webhook = None
            else:
                allowed = tuple(
                    host.strip()
                    for host in get_settings().feishu_webhook_allowed_hosts.split(",")
                    if host.strip()
                )
                try:
                    webhook = validate_feishu_webhook_url(raw, allowed_hosts=allowed)
                except ValueError as exc:
                    raise ApiError(
                        "INVALID_WEBHOOK_URL", "Feishu webhook URL is invalid.", 400
                    ) from exc
        nested.pop("feishu_webhook_configured", None)
        normalized["notification_settings"] = nested
    if "feishu_webhook" in normalized:
        raw = normalized.pop("feishu_webhook")
        if raw is None:
            webhook = None
        else:
            allowed = tuple(
                host.strip()
                for host in get_settings().feishu_webhook_allowed_hosts.split(",")
                if host.strip()
            )
            try:
                webhook = validate_feishu_webhook_url(raw, allowed_hosts=allowed)
            except ValueError as exc:
                raise ApiError(
                    "INVALID_WEBHOOK_URL", "Feishu webhook URL is invalid.", 400
                ) from exc
    return normalized, webhook


def _legacy_parameter_aliases(indicator: str, raw: Any) -> dict[str, Any]:
    values = dict(raw) if isinstance(raw, dict) else {}
    if indicator == "MA" and ("short" in values or "long" in values):
        periods = [values[key] for key in ("short", "long") if key in values]
        return {"periods": periods}
    if indicator == "BOLL" and "stddev" in values:
        values["multiplier"] = values.pop("stddev")
    if indicator == "MACD":
        aliases = {"fast": "fast_period", "slow": "slow_period", "signal": "signal_period"}
        for old, new in aliases.items():
            if old in values:
                values[new] = values.pop(old)
    return values


def _legacy_webhook(settings: dict[str, Any]) -> str | None:
    nested = settings.get("notification_settings") if isinstance(settings, dict) else None
    if not isinstance(nested, dict):
        nested = settings.get("notification") if isinstance(settings, dict) else None
    raw = nested.get("feishu_webhook") if isinstance(nested, dict) else None
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _strip_legacy_webhook(settings: dict[str, Any]) -> dict[str, Any]:
    result = dict(settings or {})
    for key in ("notification_settings", "notification"):
        nested = result.get(key)
        if isinstance(nested, dict):
            nested = dict(nested)
            nested.pop("feishu_webhook", None)
            result[key] = nested
    result.pop("feishu_webhook", None)
    return result


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    public = _strip_legacy_webhook(settings)
    nested = public.get("notification_settings")
    if not isinstance(nested, dict):
        nested = {}
    nested = dict(nested)
    nested.pop("feishu_webhook", None)
    nested.pop("feishu_webhook_configured", None)
    public["notification_settings"] = nested
    public.pop("notification", None)
    return public


__all__ = ["UserSettingsService"]
