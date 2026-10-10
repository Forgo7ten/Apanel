"""Regression coverage for key loading, encrypted settings and login identity."""

from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import hash_password
from app.models import User, UserSetting, UserStatus
from app.schemas.auth import LoginRequest
from app.security.keyring import parse_secret_keyring
from app.security.secret_cipher import SecretCipher
from app.services.auth_service import _reserve_login_identifiers, authenticate_and_issue_tokens
from app.services.settings_service import UserSettingsService
from app.services.user_secret_service import UserSecretService
from tests.test_alert_persistence import alert_context as alert_context

KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
URL = "https://open.feishu.cn/open-apis/bot/v2/hook/test-only"
OTHER = "https://open.feishu.cn/open-apis/bot/v2/hook/replacement"


@pytest.mark.parametrize("padding", [True, False])
def test_startup_and_runtime_accept_the_same_keyring(padding):
    encoded = KEY if padding else KEY.rstrip("=")
    settings = Settings(
        app_env="production",
        app_secrets_key=encoded,
        app_secrets_key_version="v2",
        app_secrets_previous_keys=json.dumps({" v1 ": encoded}),
    )
    settings.validate_notification_credentials()
    cipher = SecretCipher.from_settings(settings)
    old = SecretCipher(b"k" * 32, key_version=" v1 ")
    sealed = old.encrypt("value", aad="test")
    assert cipher.decrypt(sealed, aad="test", key_version=" v1 ") == "value"
    assert cipher.decrypt(cipher.encrypt("new", aad="test"), aad="test") == "new"


@pytest.mark.parametrize(
    "previous",
    [
        '{"v0":"' + KEY + '","v0":"' + KEY + '"}',
        json.dumps({" v0 ": KEY, "v0": KEY}),
        json.dumps({" v1 ": KEY}),
        json.dumps({"v0": 123}),
        json.dumps({"v0": "not+urlsafe/"}),
    ],
)
def test_keyring_rejects_ambiguous_or_invalid_previous_keys(previous):
    settings = Settings(
        app_env="production", app_secrets_key=KEY, app_secrets_previous_keys=previous
    )
    with pytest.raises(ValueError):
        settings.validate_notification_credentials()
    with pytest.raises(ValueError):
        SecretCipher.from_settings(settings)


def test_keyring_repr_never_contains_key_material():
    ring = parse_secret_keyring(KEY, "v1", None)
    assert KEY not in repr(ring)
    assert repr(b"k" * 32) not in repr(ring)


async def test_unrelated_settings_update_preserves_only_encrypted_webhook(alert_context):
    uid = alert_context["user_one"].id
    async with alert_context["session_factory"]() as session:
        secrets = UserSecretService(session)
        await secrets.set_feishu_webhook(uid, URL)
        await session.commit()
        updated = await UserSettingsService(session).update(
            uid, {"display_settings": {"density": "compact"}}
        )
        assert updated.settings["notification_settings"]["feishu_webhook_configured"]
        assert await secrets.get_feishu_webhook(uid) == URL
        row = (
            await session.execute(select(UserSetting).where(UserSetting.user_id == uid))
        ).scalar_one()
        assert "feishu_webhook" not in str(row.settings)


async def test_unrelated_settings_update_preserves_existing_secret_and_null_clears(alert_context):
    uid = alert_context["user_one"].id
    async with alert_context["session_factory"]() as session:
        secrets = UserSecretService(session)
        await secrets.set_feishu_webhook(uid, OTHER)
        await session.commit()
        service = UserSettingsService(session)
        await service.update(uid, {"adjust_type": "none"})
        assert await secrets.get_feishu_webhook(uid) == OTHER
        await service.update(uid, {"notification_settings": {"feishu_webhook": None}})
        assert await secrets.get_feishu_webhook(uid) is None


async def test_cipher_failure_rolls_back_settings_and_keeps_previous_secret(
    alert_context, monkeypatch
):
    uid = alert_context["user_one"].id
    async with alert_context["session_factory"]() as session:
        service = UserSettingsService(session)
        await service.update(uid, {"adjust_type": "qfq"})
        await service.secret_service.set_feishu_webhook(uid, OTHER)
        await session.commit()

        def fail(*args, **kwargs):
            raise RuntimeError("simulated cipher failure")

        monkeypatch.setattr(service.secret_service.cipher, "encrypt", fail)
        with pytest.raises(RuntimeError):
            await service.update(
                uid,
                {"adjust_type": "none", "notification_settings": {"feishu_webhook": URL}},
            )
        row = (
            await session.execute(select(UserSetting).where(UserSetting.user_id == uid))
        ).scalar_one()
        assert row.settings["adjust_type"] == "qfq"
        assert await service.secret_service.get_feishu_webhook(uid) == OTHER


@pytest.mark.parametrize(
    "payload",
    [
        {"notification": {"feishu_webhook": URL}},
        {"feishu_webhook": URL},
        {"settings": {"notification_settings": {"feishu_webhook": URL}}},
    ],
)
async def test_obsolete_webhook_request_shapes_are_rejected(alert_context, payload):
    uid = alert_context["user_one"].id
    async with alert_context["session_factory"]() as session:
        service = UserSettingsService(session)
        with pytest.raises(ApiError, match="current typed"):
            await service.update(uid, payload)
        assert await service.secret_service.get_feishu_webhook(uid) is None


async def test_json_settings_never_return_or_rewrite_raw_secrets(alert_context):
    uid = alert_context["user_one"].id
    async with alert_context["session_factory"]() as session:
        session.add(UserSetting(user_id=uid, settings={"feishu_webhook": URL}))
        await session.commit()
        service = UserSettingsService(session)
        public = await service.get(uid)
        assert URL not in str(public.settings)
        updated = await service.update(uid, {"adjust_type": "none"})
        assert URL not in str(updated.settings)
        row = (
            await session.execute(select(UserSetting).where(UserSetting.user_id == uid))
        ).scalar_one()
        assert URL not in str(row.settings)


async def test_ambiguous_login_identifier_is_fail_closed_and_explicit_mode_is_supported(
    alert_context,
):
    uid = alert_context["user_one"].id
    factory = alert_context["session_factory"]
    async with factory() as session:
        first = await session.get(User, uid)
        first.password_hash = hash_password("strong-password")
        collision = first.email
        session.add(
            User(
                username=collision,
                email="different@example.com",
                password_hash=hash_password("different-password"),
                status=UserStatus.ACTIVE,
            )
        )
        await session.commit()
    settings = Settings(app_env="test")
    async with factory() as session:
        with pytest.raises(ApiError) as exc:
            await authenticate_and_issue_tokens(
                session,
                LoginRequest(username=collision, password="strong-password"),
                settings=settings,
            )
        assert exc.value.code == "INVALID_CREDENTIALS"
    async with factory() as session:
        token = await authenticate_and_issue_tokens(
            session,
            LoginRequest(username=collision, password="strong-password", identifier_type="email"),
            settings=settings,
        )
        assert token.user.id == uid


async def test_new_account_cannot_reuse_another_users_email_as_username(alert_context):
    async with alert_context["session_factory"]() as session:
        with pytest.raises(ApiError):
            await _reserve_login_identifiers(session, "ALERTS-ONE@EXAMPLE.COM", "new@example.com")
