from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli import build_parser
from app.core.config import Settings
from app.db.base import Base
from app.models import User, UserSecret, UserSetting, UserStatus
from app.services.notification_service import NotificationService
from app.services.settings_service import UserSettingsService
from app.services.user_secret_service import FEISHU_WEBHOOK_SECRET, audit_user_secrets


@pytest.mark.asyncio
async def test_user_secret_audit_reports_release_b_readiness(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'secret-audit.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        first = User(
            username="legacy-user",
            email="legacy@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        second = User(
            username="encrypted-user",
            email="encrypted@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        session.add_all([first, second])
        await session.flush()
        session.add(
            UserSetting(
                user_id=first.id,
                settings={
                    "notification_settings": {
                        "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/legacy"
                    }
                },
            )
        )
        session.add(
            UserSecret(
                user_id=second.id,
                secret_type=FEISHU_WEBHOOK_SECRET,
                ciphertext="opaque",
                key_version="v1",
            )
        )
        await session.commit()

        audit = await audit_user_secrets(session)
        assert audit.legacy_plaintext_count == 1
        assert audit.encrypted_count == 1
        assert audit.legacy_without_encrypted_count == 1
        assert audit.release_b_ready is False

        setting = await session.get(UserSetting, 1)
        assert setting is not None
        setting.settings = {"notification_settings": {}}
        await session.commit()
        clean = await audit_user_secrets(session)
        assert clean.legacy_plaintext_count == 0
        assert clean.release_b_ready is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_webhook_fallback_can_be_disabled(tmp_path, monkeypatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'secret-fallback.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            username="legacy-only",
            email="legacy-only@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserSetting(
                user_id=user.id,
                settings={
                    "notification_settings": {
                        "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/legacy"
                    }
                },
            )
        )
        await session.commit()

        disabled = Settings(app_env="test", legacy_webhook_fallback_enabled=False)
        monkeypatch.setattr("app.services.notification_service.get_settings", lambda: disabled)
        monkeypatch.setattr("app.services.settings_service.get_settings", lambda: disabled)

        assert await NotificationService(session)._user_webhook(user.id) is None
        public = await UserSettingsService(session).get(user.id)
        assert public.settings["notification_settings"]["feishu_webhook_configured"] is False
    await engine.dispose()


def test_cli_exposes_user_secret_audit_command() -> None:
    assert build_parser().parse_args(["audit-user-secrets"]).command == "audit-user-secrets"
