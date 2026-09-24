from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import (
    AlertRule,
    IndicatorSnapshot,
    Notification,
    Security,
    User,
    UserStatus,
)
from app.tasks.contracts import PipelineConfigurationError, PipelineContext
from app.tasks.pipeline import build_default_eod_steps


class FakeMarketDataClient:
    async def sync_daily(self, **_kwargs):
        return {"ok": True}


class FakeNotificationProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def send(self, _user, _message) -> None:
        self.calls += 1


@pytest_asyncio.fixture
async def pipeline_context(tmp_path) -> AsyncIterator[dict[str, object]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pipeline.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            username="pipeline-user",
            email="pipeline-user@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        security = Security(
            symbol="600519",
            name="贵州茅台",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add_all([user, security])
        await session.flush()
        session.add_all(
            [
                AlertRule(
                    user_id=user.id,
                    security_id=security.id,
                    condition_type="VALUE",
                    indicator_type="RSI",
                    operator=">=",
                    threshold=70,
                    enabled=True,
                ),
                IndicatorSnapshot(
                    security_id=security.id,
                    trade_date=date(2026, 9, 24),
                    indicator_type="RSI",
                    parameters={"period": 14},
                    values={"value": 71.0},
                    previous_values={"value": 69.0},
                    delta={"value": 2.0},
                ),
            ]
        )
        await session.commit()

    provider = FakeNotificationProvider()
    context = PipelineContext(
        date(2026, 9, 24),
        "qfq",
        ("600519",),
        resources={
            "session_factory": session_factory,
            "notification_provider": provider,
        },
    )
    yield {
        "engine": engine,
        "session_factory": session_factory,
        "context": context,
        "provider": provider,
    }
    await engine.dispose()


def _default_steps(session_factory, provider):
    return build_default_eod_steps(
        session_factory=session_factory,
        market_data_client=FakeMarketDataClient(),
        notification_provider=provider,
    )


@pytest.mark.asyncio
async def test_default_alert_and_notification_adapters_trigger_once(pipeline_context) -> None:
    session_factory = pipeline_context["session_factory"]
    provider = pipeline_context["provider"]
    context = pipeline_context["context"]
    steps = _default_steps(session_factory, provider)

    first_alerts = await steps[-2].run(context)
    context.artifacts["alerts"] = first_alerts
    first_summary = await steps[-1].run(context)

    assert len(first_alerts) == 1
    assert first_alerts[0].triggered is True
    assert first_summary == {"total": 1, "sent": 1, "failed": 0, "pending": 0}
    assert provider.calls == 1

    second_context = PipelineContext(
        date(2026, 9, 24),
        "qfq",
        ("600519",),
        resources={
            "session_factory": session_factory,
            "notification_provider": provider,
        },
    )
    second_alerts = await steps[-2].run(second_context)
    second_context.artifacts["alerts"] = second_alerts
    second_summary = await steps[-1].run(second_context)

    assert second_alerts[0].triggered is False
    assert second_summary == {"total": 0, "sent": 0, "failed": 0, "pending": 0}
    assert provider.calls == 1
    async with session_factory() as session:
        assert len(list((await session.execute(select(Notification))).scalars())) == 1


@pytest.mark.asyncio
async def test_default_alert_adapter_fails_explicitly_without_session_factory() -> None:
    context = PipelineContext(
        date(2026, 9, 24),
        "qfq",
        ("600519",),
        resources={"session_factory": None},
    )
    steps = _default_steps(None, None)

    with pytest.raises(PipelineConfigurationError, match="alert session factory"):
        await steps[-2].run(context)
