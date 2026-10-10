"""TDD coverage for multi-parameter indicator snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.db.base import Base
from app.indicators.parameters import (
    MAX_INDICATOR_PERIOD,
    MAX_MA_PERIODS,
    canonicalize_parameters,
    merge_indicator_requests,
    parameter_key,
    required_history_bars,
    select_snapshot_variant,
    snapshot_variants,
)
from app.models import (
    CurrentIndicatorState,
    DailyBar,
    IndicatorSnapshot,
    Security,
    TableColumn,
    TradingCalendar,
    User,
    UserStatus,
    WatchTable,
    WatchTableSymbol,
)
from app.services.indicator_service import IndicatorService
from app.services.state_service import StateService
from app.tasks.contracts import PipelineContext
from app.tasks.pipeline import EODPipeline, build_default_eod_steps, collect_indicator_requests


def test_parameter_keys_are_canonical_and_flat_rows_are_not_supported() -> None:
    first = canonicalize_parameters("boll", {"period": 20, "stddev": 2})
    second = canonicalize_parameters("BOLLINGER", {"multiplier": 2.0, "period": 20})

    assert first == second == {"period": 20, "multiplier": 2.0}
    assert parameter_key("BOLL", first) == parameter_key("BOLLINGER", second)

    obsolete = SimpleNamespace(
        indicator_type="RSI",
        parameters={"period": 14},
        values={"value": 71.0},
        previous_values={"value": 69.0},
        delta={"value": 2.0},
    )
    with pytest.raises(ApiError, match="format"):
        snapshot_variants(obsolete)
    with pytest.raises(ApiError, match="format"):
        select_snapshot_variant(obsolete, "RSI", {"period": 14})

    key = parameter_key("RSI", {"period": 14})
    current = SimpleNamespace(
        indicator_type="RSI",
        parameters={"_format": 2, "variants": {key: {"period": 14}}},
        values={key: {"value": 71.0}},
        previous_values={key: {"value": 69.0}},
        delta={key: {"value": 2.0}},
    )
    assert snapshot_variants(current)[0].values == {"value": 71.0}
    assert select_snapshot_variant(current, "RSI", {"period": 14}) == snapshot_variants(current)[0]


def test_indicator_periods_are_bounded_by_market_data_bootstrap_contract() -> None:
    with pytest.raises(ApiError, match=str(MAX_INDICATOR_PERIOD)):
        canonicalize_parameters("MA", {"period": MAX_INDICATOR_PERIOD + 1})
    with pytest.raises(ApiError, match=str(MAX_INDICATOR_PERIOD)):
        canonicalize_parameters("RSI", {"period": MAX_INDICATOR_PERIOD + 1})


def test_ma_period_count_is_bounded_to_avoid_quadratic_state_explosion() -> None:
    with pytest.raises(ApiError, match=str(MAX_MA_PERIODS)):
        canonicalize_parameters("MA", {"periods": list(range(1, MAX_MA_PERIODS + 2))})


def test_distributed_ma_requests_do_not_collapse_into_one_oversized_bundle() -> None:
    merged = merge_indicator_requests([("MA", {"period": period}) for period in range(1, 34)])
    ma_requests = [item for item in merged if item.indicator_type == "MA"]

    assert max(len(item.parameters["periods"]) for item in ma_requests) <= MAX_MA_PERIODS
    assert {period for item in ma_requests for period in item.parameters["periods"]} >= set(
        range(1, 34)
    )
    assert required_history_bars(merged) >= 33


def test_explicit_ma_pair_survives_when_wider_numeric_bundle_contains_it() -> None:
    merged = merge_indicator_requests(
        [
            ("MA", {"periods": [5, 20]}),
            ("MA", {"periods": [5, 10, 20]}),
        ]
    )

    ma_parameters = {
        tuple(item.parameters["periods"]) for item in merged if item.indicator_type == "MA"
    }
    assert (5, 20) in ma_parameters
    assert (5, 10, 20) in ma_parameters


@pytest_asyncio.fixture
async def multi_parameter_context(
    tmp_path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'multi-parameter.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        security = Security(
            symbol="600519",
            name="示例股票",
            market="SH",
            exchange="SH",
            security_type="STOCK",
            status="ACTIVE",
        )
        session.add(security)
        await session.flush()
        calendar_day = date(2026, 1, 1)
        while calendar_day <= date(2026, 2, 28):
            session.add(
                TradingCalendar(
                    market="CN",
                    trade_date=calendar_day,
                    expected_open=True,
                    actual_open=True,
                    status="OPEN",
                    source="TEST",
                    source_metadata={},
                )
            )
            calendar_day += timedelta(days=1)
        for index in range(45):
            close = Decimal(str(100 + index * 0.2))
            session.add(
                DailyBar(
                    security_id=security.id,
                    trade_date=date(2026, 1, 1) + timedelta(days=index),
                    open=close,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=100,
                    amount=10000,
                    adjust_type="qfq",
                )
            )
        await session.commit()
    yield session_factory
    await engine.dispose()


async def test_many_independent_ma_columns_do_not_create_cross_product_states(
    multi_parameter_context,
) -> None:
    requests = [("MA", {"period": period}) for period in range(1, 34)]
    async with multi_parameter_context() as session:
        await IndicatorService(session).calculate("600519", requests=requests)
        await StateService(session).calculate("600519")
        states = list(
            (
                await session.execute(
                    select(CurrentIndicatorState).where(
                        CurrentIndicatorState.indicator_type == "MA"
                    )
                )
            ).scalars()
        )

    assert states
    assert {
        (item.parameters["short_period"], item.parameters["long_period"]) for item in states
    } == {(5, 10)}


async def test_calculation_persists_one_v2_row_per_type_with_all_requested_variants(
    multi_parameter_context,
) -> None:
    requests = [
        ("MA", {"period": 5}),
        ("MA", {"period": 20}),
        ("RSI", {"period": 6}),
        ("RSI", {"period": 14}),
        ("BOLL", {"period": 20, "multiplier": 2}),
        ("BOLL", {"period": 20, "multiplier": 3}),
    ]
    async with multi_parameter_context() as session:
        first = await IndicatorService(session).calculate("600519", requests=requests)
        latest_date = max(item.trade_date for item in first)
        latest_rows = [item for item in first if item.trade_date == latest_date]

        assert {item.indicator_type for item in latest_rows} == {
            "MA",
            "PROJECTED_MA",
            "RSI",
            "KDJ",
            "BOLL",
            "MACD",
        }
        assert len(latest_rows) == 6

        ma = next(item for item in latest_rows if item.indicator_type == "MA")
        rsi = next(item for item in latest_rows if item.indicator_type == "RSI")
        boll = next(item for item in latest_rows if item.indicator_type == "BOLL")
        assert ma.parameters["_format"] == 2
        assert len(ma.parameters["variants"]) == 3
        assert {tuple(value["periods"]) for value in ma.parameters["variants"].values()} == {
            (5, 10),
            (5,),
            (20,),
        }
        assert len(rsi.parameters["variants"]) == 2
        assert len(boll.parameters["variants"]) == 2
        assert set(rsi.values) == set(rsi.parameters["variants"])
        assert set(boll.values) == set(boll.parameters["variants"])

        row_count = (
            await session.execute(select(func.count()).select_from(IndicatorSnapshot))
        ).scalar_one()
        second = await IndicatorService(session).calculate("600519", requests=requests)
        second_count = (
            await session.execute(select(func.count()).select_from(IndicatorSnapshot))
        ).scalar_one()

        assert second_count == row_count
        assert len(second) == len(first)


async def test_history_expands_parameter_variants_without_mixing_values(
    multi_parameter_context,
) -> None:
    requests = [
        ("MA", {"period": 5}),
        ("MA", {"period": 20}),
        ("RSI", {"period": 6}),
        ("RSI", {"period": 14}),
        ("BOLL", {"period": 20, "multiplier": 2}),
        ("BOLL", {"period": 20, "multiplier": 3}),
    ]
    async with multi_parameter_context() as session:
        service = IndicatorService(session)
        await service.calculate("600519", requests=requests)
        history = await service.history_data(
            "600519", start=date(2026, 2, 10), end=date(2026, 2, 14)
        )

        rows = [item for item in history.items if item.trade_date == date(2026, 2, 14)]
        assert len([item for item in rows if item.indicator_type == "RSI"]) == 2
        assert len([item for item in rows if item.indicator_type == "BOLL"]) == 2
        assert {item.parameters["period"] for item in rows if item.indicator_type == "RSI"} == {
            6,
            14,
        }
        assert {
            item.parameters["multiplier"] for item in rows if item.indicator_type == "BOLL"
        } == {2.0, 3.0}
        assert len([item for item in rows if item.indicator_type == "MA"]) >= 2
        assert all(item.parameter_key.startswith("v1_") for item in rows)

        selectors = (
            ("MA", {"periods": [5]}),
            ("MA", {"periods": [20]}),
            ("RSI", {"period": 6}),
            ("RSI", {"period": 14}),
            ("BOLL", {"period": 20, "multiplier": 2.0}),
            ("BOLL", {"period": 20, "multiplier": 3.0}),
        )
        for indicator_type, parameters in selectors:
            source = next(
                item
                for item in rows
                if item.indicator_type == indicator_type and item.parameters == parameters
            )
            selected = await service.history_data(
                "600519",
                start=date(2026, 2, 14),
                end=date(2026, 2, 14),
                parameter_key=source.parameter_key,
            )
            assert selected.items
            assert {item.parameter_key for item in selected.items} == {source.parameter_key}
            assert all(item.parameters == parameters for item in selected.items)


async def test_pipeline_collects_column_parameters_and_keeps_default_requests() -> None:
    class Result:
        def all(self):
            return [
                ("600519", "RSI", {"period": 6}),
                ("600519", "BOLL", {"period": 20, "multiplier": 3}),
                ("600000", "MA", {"period": 20}),
            ]

    class Session:
        async def execute(self, _statement):
            return Result()

    collected = await collect_indicator_requests(Session(), ("600519", "600000"))

    assert [item.parameters for item in collected["600519"] if item.indicator_type == "RSI"] == [
        {"period": 14},
        {"period": 6},
    ]
    assert any(
        item.indicator_type == "BOLL" and item.parameters == {"period": 20, "multiplier": 3.0}
        for item in collected["600519"]
    )
    ma_parameters = {
        tuple(item.parameters["periods"])
        for item in collected["600000"]
        if item.indicator_type == "MA"
    }
    assert ma_parameters == {(5, 10), (20,)}


def test_alert_target_selects_the_exact_parameter_variant() -> None:
    snapshot = SimpleNamespace(
        trade_date=date(2026, 9, 24),
        indicator_type="RSI",
        parameters={
            "_format": 2,
            "variants": {
                parameter_key("RSI", {"period": 6}): {"period": 6},
                parameter_key("RSI", {"period": 14}): {"period": 14},
            },
        },
        values={
            parameter_key("RSI", {"period": 6}): {"value": 95.0},
            parameter_key("RSI", {"period": 14}): {"value": 65.0},
        },
        previous_values={},
        delta={},
    )
    selected = select_snapshot_variant(snapshot, "RSI", {"period": 6})
    assert selected is not None
    assert selected.parameters == {"period": 6}
    assert selected.values["value"] == 95.0


async def test_state_recalculation_keeps_custom_variants_and_default_state_semantics(
    multi_parameter_context,
) -> None:
    requests = [("RSI", {"period": 6}), ("MA", {"period": 20})]
    async with multi_parameter_context() as session:
        await IndicatorService(session).calculate("600519", requests=requests)
        first_states = await StateService(session).calculate("600519")
        rows = await session.execute(select(IndicatorSnapshot))
        latest_date = max(item.trade_date for item in rows.scalars())
        latest = list(
            (
                await session.execute(
                    select(IndicatorSnapshot).where(IndicatorSnapshot.trade_date == latest_date)
                )
            ).scalars()
        )
        second_states = await StateService(session).calculate("600519")

        rsi = next(item for item in latest if item.indicator_type == "RSI")
        assert len(rsi.parameters["variants"]) == 2
        assert [item.status for item in first_states] == [item.status for item in second_states]


async def test_eod_pipeline_reuses_one_snapshot_row_and_keeps_column_demand(
    multi_parameter_context,
) -> None:
    async with multi_parameter_context() as session:
        user = User(
            username="pipeline-columns",
            email="pipeline-columns@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        security = (await session.execute(select(Security))).scalar_one()
        table = WatchTable(user_id=1, name="EOD 参数")
        session.add_all([user, table])
        await session.flush()
        table.user_id = user.id
        session.add_all(
            [
                WatchTableSymbol(watch_table_id=table.id, security_id=security.id, position=0),
                TableColumn(
                    watch_table_id=table.id,
                    column_type="INDICATOR",
                    indicator_type="RSI",
                    parameters={"period": 6},
                    view_mode="NUMBER",
                    position=0,
                ),
                TableColumn(
                    watch_table_id=table.id,
                    column_type="INDICATOR",
                    indicator_type="BOLL",
                    parameters={"period": 20, "multiplier": 3},
                    view_mode="COMPOSITE",
                    position=1,
                ),
            ]
        )
        await session.commit()

    class SyncResult:
        items = ()
        ok = True
        retryable_symbols = ()

    class MarketData:
        async def sync_daily(self, **_kwargs):
            return SyncResult()

        async def sync_dividends(self, **_kwargs):
            return SyncResult()

    async def alert_runner(_context):
        return []

    async def notification_runner(_context):
        return {"total": 0, "sent": 0, "failed": 0, "pending": 0}

    steps = build_default_eod_steps(
        session_factory=multi_parameter_context,
        market_data_client=MarketData(),
        alert_runner=alert_runner,
        notification_runner=notification_runner,
    )
    first = await EODPipeline(steps).run(PipelineContext(date(2026, 2, 14), "qfq", ("600519",)))
    second = await EODPipeline(steps).run(PipelineContext(date(2026, 2, 14), "qfq", ("600519",)))

    assert first.completed_steps == second.completed_steps
    async with multi_parameter_context() as session:
        rows = list((await session.execute(select(IndicatorSnapshot))).scalars())
        latest_date = max(item.trade_date for item in rows)
        latest = [item for item in rows if item.trade_date == latest_date]
        assert len(rows) == len(
            set((item.security_id, item.trade_date, item.indicator_type) for item in rows)
        )
        rsi = next(item for item in latest if item.indicator_type == "RSI")
        boll = next(item for item in latest if item.indicator_type == "BOLL")
        assert len(rsi.parameters["variants"]) == 2
        assert len(boll.parameters["variants"]) == 2
