from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clients.market_data_hub import SyncItem, SyncResult
from app.core.config import Settings
from app.db.base import Base
from app.models import Security
from app.services.data_bootstrap_service import BootstrapResult, SecurityDataBootstrapService
from app.services.indicator_service import IndicatorService
from app.services.state_service import StateService
from app.tasks import jobs


class _SessionFactory:
    def __call__(self):
        return _SessionContext()


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DividendFailingClient:
    async def sync_daily(self, **_kwargs):
        return SyncResult("daily", (SyncItem("600519", "success"),))

    async def sync_dividends(self, **_kwargs):
        return SyncResult("dividend", (SyncItem("600519", "failed", error_code="PROVIDER_UNAVAILABLE"),))


@pytest.mark.asyncio
async def test_bootstrap_requires_dividend_sync_before_completion(monkeypatch) -> None:
    async def no_indicators(self, *_args, **_kwargs):
        return []

    async def no_states(self, *_args, **_kwargs):
        return []

    monkeypatch.setattr(IndicatorService, "rebuild_history", no_indicators)
    monkeypatch.setattr(StateService, "rebuild_history", no_states)

    service = SecurityDataBootstrapService(
        session_factory=_SessionFactory(),
        market_data_client=_DividendFailingClient(),
        minimum_bars=1,
    )

    with pytest.raises(RuntimeError, match="dividend bootstrap did not complete"):
        await service.ensure_ready("600519", adjustments=("qfq",), requests=())


class _FakeLock:
    def __init__(self, *_args, **_kwargs) -> None:
        self.released = False

    async def acquire(self) -> bool:
        return True

    async def release(self) -> None:
        self.released = True


@pytest.mark.asyncio
async def test_execute_bootstrap_marks_security_only_after_success(tmp_path, monkeypatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bootstrap-complete.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        security = Security(
            symbol="600519",
            name="贵州茅台",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add(security)
        await session.commit()
        security_id = security.id

    async def successful_bootstrap(self, symbol, **_kwargs):
        return BootstrapResult(
            symbol=symbol,
            adjustments=("qfq",),
            dividend_synced=True,
            indicators_materialized=1,
            states_materialized=1,
        )

    monkeypatch.setattr(jobs, "RedisTaskLock", _FakeLock)
    monkeypatch.setattr(SecurityDataBootstrapService, "ensure_ready", successful_bootstrap)

    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite://",
        jwt_secret_key="test-secret-that-is-at-least-32-bytes-long",
        refresh_cookie_secure=False,
    )
    result = await jobs.execute_security_bootstrap(
        "600519",
        settings=settings,
        session_factory=factory,
        market_data_client=SimpleNamespace(),
        redis_client=SimpleNamespace(),
    )

    assert result["status"] == "completed"
    async with factory() as session:
        refreshed = await session.get(Security, security_id)
        assert refreshed is not None
        assert refreshed.bootstrap_completed_at is not None

    await engine.dispose()
