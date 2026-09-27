from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.indicators.parameters import IndicatorRequest
from app.models import DailyBar, Security
from app.services.data_readiness_service import DataReadinessService


class FakeBootstrapService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], tuple[IndicatorRequest, ...]]] = []

    async def ensure_ready(self, symbol, *, adjustments, requests):
        self.calls.append((symbol, tuple(adjustments), tuple(requests)))
        return None


@pytest.mark.asyncio
async def test_readiness_bootstraps_only_missing_adjustments(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'readiness.db'}")
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
        await session.flush()
        for adjustment, count in (("qfq", 34), ("none", 1)):
            for index in range(count):
                close = Decimal("100") + index
                session.add(
                    DailyBar(
                        security_id=security.id,
                        trade_date=date(2026, 9, 20) + timedelta(days=index),
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=100,
                        amount=1000,
                        adjust_type=adjustment,
                    )
                )
        await session.commit()

    bootstrap = FakeBootstrapService()
    result = await DataReadinessService(
        session_factory=factory,
        bootstrap_service=bootstrap,
        minimum_bars=3,
    ).ensure_minimum_history(
        ("600519",),
        adjustments=("qfq", "none"),
        requests_by_symbol={"600519": ()},
    )

    assert result.bootstrapped_symbols == ("600519",)
    assert result.required_bars == {"600519": 34}
    assert bootstrap.calls == [("600519", ("none",), ())]
    await engine.dispose()


@pytest.mark.asyncio
async def test_readiness_uses_custom_indicator_depth(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'custom-depth.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        security = Security(
            symbol="000001",
            name="平安银行",
            market="SZ",
            exchange="SZ",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add(security)
        await session.flush()
        for index in range(40):
            close = Decimal("10") + Decimal(index) / 10
            session.add(
                DailyBar(
                    security_id=security.id,
                    trade_date=date(2026, 7, 1) + timedelta(days=index),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=100,
                    amount=1000,
                    adjust_type="qfq",
                )
            )
        await session.commit()

    bootstrap = FakeBootstrapService()
    result = await DataReadinessService(
        session_factory=factory,
        bootstrap_service=bootstrap,
        minimum_bars=20,
    ).ensure_minimum_history(
        ("000001",),
        adjustments=("qfq",),
        requests_by_symbol={"000001": (IndicatorRequest("MA", {"period": 60}),)},
    )

    assert result.required_bars == {"000001": 60}
    assert result.bootstrapped_symbols == ("000001",)
    assert bootstrap.calls[0][1] == ("qfq",)
    await engine.dispose()
