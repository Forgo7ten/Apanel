"""Publication, cutoff and durable-demand regressions over real application services."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select

from app.core.errors import ApiError
from app.indicators.parameters import IndicatorRequest, select_snapshot_variant
from app.models import (
    AnalysisStream,
    CurrentIndicatorState,
    DailyBar,
    IndicatorSnapshot,
    TableColumn,
    User,
    UserStatus,
    WatchTable,
    WatchTableSymbol,
)
from app.schemas.watch import ColumnCreateRequest, ColumnUpdateRequest
from app.services.analysis_control import mark_demand_changed
from app.services.analysis_demand import collect_indicator_requests
from app.services.analysis_service import AnalysisService
from app.services.data_readiness_service import DataReadinessService
from app.services.indicator_service import IndicatorService
from app.services.state_service import StateService
from app.services.watch_table_service import WatchTableService
from app.tasks.contracts import PipelineContext
from app.tasks.pipeline import _sync_required_history_in_chunks
from tests.test_indicator_state_persistence import indicator_context as indicator_context

END = date(2026, 2, 9)


async def test_failure_in_state_rolls_back_indicator_changes_and_keeps_dirty(
    indicator_context, monkeypatch
):
    factory = indicator_context
    async with factory() as session:
        await AnalysisService(session).materialize("600519", end=END)
    async with factory() as session:
        stream = (await session.execute(select(AnalysisStream))).scalar_one()
        old_revision = stream.published_revision
        stream.source_revision += 1
        stream.dirty_from = date(2026, 2, 7)
        before = (await session.execute(select(IndicatorSnapshot.id))).scalars().all()
        await session.commit()

    async def broken(*args, **kwargs):
        raise RuntimeError("state failed after indicator writes")

    monkeypatch.setattr(StateService, "rebuild_history", broken)
    async with factory() as session:
        with pytest.raises(RuntimeError):
            await AnalysisService(session).materialize("600519", end=END)
    async with factory() as session:
        after = (await session.execute(select(IndicatorSnapshot.id))).scalars().all()
        assert after == before
        stream = (await session.execute(select(AnalysisStream))).scalar_one()
        assert stream.published_revision == old_revision
        assert stream.dirty_from == date(2026, 2, 7)


async def test_regular_ohlc_correction_rebuilds_yesterday_and_today_consistently(indicator_context):
    factory = indicator_context
    async with factory() as session:
        await AnalysisService(session).materialize("600519", end=END)
    async with factory() as session:
        bar = (
            await session.execute(
                select(DailyBar).where(
                    DailyBar.trade_date == END - timedelta(days=1), DailyBar.adjust_type == "qfq"
                )
            )
        ).scalar_one()
        bar.close = bar.open = Decimal("130")
        bar.high, bar.low = Decimal("131"), Decimal("129")
        stream = (await session.execute(select(AnalysisStream))).scalar_one()
        stream.source_revision += 1
        stream.dirty_from = bar.trade_date
        await session.commit()
    async with factory() as session:
        result = await AnalysisService(session).materialize("600519", end=END)
        assert result.indicators == 12
        snapshots = (
            (
                await session.execute(
                    select(IndicatorSnapshot).where(
                        IndicatorSnapshot.indicator_type == "RSI",
                        IndicatorSnapshot.adjust_type == "qfq",
                        IndicatorSnapshot.trade_date.in_([END - timedelta(days=1), END]),
                    )
                )
            )
            .scalars()
            .all()
        )
        variants = {
            row.trade_date: select_snapshot_variant(row, "RSI", {"period": 14}) for row in snapshots
        }
        assert variants[END - timedelta(days=1)].values["value"] == pytest.approx(
            variants[END].previous_values["value"]
        )
        stream = (await session.execute(select(AnalysisStream))).scalar_one()
        assert stream.dirty_from is None
        assert stream.source_revision == stream.published_revision


async def test_asof_applies_to_all_layers_and_older_tasks_cannot_regress_published_current(
    indicator_context,
):
    factory = indicator_context
    cutoff = date(2026, 1, 30)
    async with factory() as session:
        await AnalysisService(session).materialize("600519", end=cutoff)
        assert (
            await session.execute(select(func.max(IndicatorSnapshot.trade_date)))
        ).scalar_one() == cutoff
        assert (
            await session.execute(select(func.max(CurrentIndicatorState.trade_date)))
        ).scalar_one() == cutoff
    async with factory() as session:
        await AnalysisService(session).materialize("600519", end=END)
        with pytest.raises(ApiError) as exc:
            await AnalysisService(session).materialize("600519", end=cutoff)
        assert exc.value.code == "STALE_ANALYSIS_TARGET"
        assert (
            await session.execute(select(func.max(IndicatorSnapshot.trade_date)))
        ).scalar_one() == END


async def test_unchanged_stream_is_noop_but_new_parameter_backfills_history(indicator_context):
    factory = indicator_context
    async with factory() as session:
        first = await AnalysisService(session).materialize("600519", end=END)
        assert first.rebuilt
        await mark_demand_changed(session, [1])
        await session.commit()
        repeat = await AnalysisService(session).materialize("600519", end=END)
        assert repeat.indicators == repeat.states == 0
        result = await AnalysisService(session).materialize(
            "600519", end=END, requests=[IndicatorRequest("RSI", {"period": 6})]
        )
        assert result.rebuilt
        yesterday = (
            await session.execute(
                select(IndicatorSnapshot).where(
                    IndicatorSnapshot.indicator_type == "RSI",
                    IndicatorSnapshot.adjust_type == "qfq",
                    IndicatorSnapshot.trade_date == END - timedelta(days=1),
                )
            )
        ).scalar_one()
        assert select_snapshot_variant(yesterday, "RSI", {"period": 6}) is not None


async def test_invalid_current_values_are_unknown_even_after_known_active(indicator_context):
    factory = indicator_context
    async with factory() as session:
        await IndicatorService(session).calculate("600519")
        await StateService(session).calculate("600519")
    async with factory() as session:
        snapshot = (
            await session.execute(
                select(IndicatorSnapshot).where(
                    IndicatorSnapshot.indicator_type == "KDJ",
                    IndicatorSnapshot.adjust_type == "qfq",
                    IndicatorSnapshot.trade_date == END,
                )
            )
        ).scalar_one()
        snapshot.values = {
            key: {field: value for field, value in values.items() if field != "j"}
            for key, values in snapshot.values.items()
        }
        await session.commit()
        await StateService(session).materialize_latest("600519", end=END)
        state = (
            await session.execute(
                select(CurrentIndicatorState).where(
                    CurrentIndicatorState.trade_date == END,
                    CurrentIndicatorState.state_code == "KDJ_ALL_RISING",
                    CurrentIndicatorState.adjust_type == "qfq",
                )
            )
        ).scalar_one()
        assert state.status == "UNKNOWN" and state.metadata_json["reason"] == "missing_values"


async def test_hot_eod_fetches_date_overlap_not_four_hundred_bars(indicator_context):
    from app.clients.market_data_hub import SyncItem, SyncResult

    class Client:
        def __init__(self):
            self.calls = []

        async def sync_daily(self, **kwargs):
            self.calls.append(kwargs)
            return SyncResult("daily", (SyncItem("600519", "success"),))

    client = Client()
    context = PipelineContext(
        END,
        "qfq",
        ("600519",),
        resources={"market_data_client": client, "session_factory": indicator_context},
    )
    await _sync_required_history_in_chunks(
        context, adjustment="qfq", requests_by_symbol={}, minimum_bars=400
    )
    assert len(client.calls) == 1
    assert client.calls[0]["start"] == END - timedelta(days=7)
    assert "lookback_bars" not in client.calls[0]


async def test_known_short_listing_history_does_not_bootstrap_forever(indicator_context):
    factory = indicator_context
    async with factory() as session:
        session.add(AnalysisStream(security_id=1, adjust_type="qfq", history_depth=400))
        await session.commit()

    class Bootstrap:
        async def ensure_ready(self, *args, **kwargs):
            raise AssertionError("do not refetch exhausted listing history")

    result = await DataReadinessService(
        session_factory=factory, bootstrap_service=Bootstrap(), minimum_bars=400
    ).ensure_minimum_history(
        ["600519"], adjustments=["qfq"], requests_by_symbol={}, completed_through=END
    )
    assert not result.bootstrapped_symbols


async def test_ma_status_demand_and_full_column_patch_validation(indicator_context):
    factory = indicator_context
    async with factory() as session:
        user = User(
            username="watch-review",
            email="watch@example.com",
            password_hash="unused",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        table = WatchTable(user_id=user.id, name="MA states")
        session.add(table)
        await session.flush()
        session.add(WatchTableSymbol(watch_table_id=table.id, security_id=1, position=0))
        await session.commit()
        uid, tid = user.id, table.id
        service = WatchTableService(session)
        column = await service.add_column(
            uid,
            tid,
            ColumnCreateRequest(
                column_type="STATE",
                indicator_type="MA",
                view_mode="STATUS",
                state_code="MA_CROSS_UP",
                parameters={"short_period": 7, "long_period": 21},
            ),
        )
        requests = (await collect_indicator_requests(session, ["600519"]))["600519"]
        assert any(request.parameters.get("periods") == [7, 21] for request in requests)
        with pytest.raises(ApiError):
            await service.update_column(
                uid, column.id, ColumnUpdateRequest(state_code="NOT_A_STATE")
            )
        await session.rollback()
        stored = await session.get(TableColumn, column.id)
        assert stored.state_code == "MA_CROSS_UP"


async def test_batch_materialization_sql_count_is_bounded(indicator_context):
    count = 0

    def record(*args, **kwargs):
        nonlocal count
        count += 1

    engine = indicator_context.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with indicator_context() as session:
            await IndicatorService(session).calculate("600519")
            await StateService(session).calculate("600519")
        assert count < 40, f"40-bar materialization unexpectedly used {count} SQL statements"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_recovery_finds_durable_demand_without_enqueue(indicator_context):
    from app.services.analysis_recovery import pending_analysis_symbols

    async with indicator_context() as session:
        await AnalysisService(session).materialize("600519", end=END)
        await mark_demand_changed(session, [1])
        await session.commit()
        found = await pending_analysis_symbols(session, ["600519"], ["qfq"], END)
        assert found == ("600519",)


async def test_current_price_fallback_never_uses_adjusted_bar(indicator_context):
    from sqlalchemy import delete

    from app.repositories.security import SecurityRepository

    async with indicator_context() as session:
        await session.execute(delete(DailyBar).where(DailyBar.adjust_type == "none"))
        await session.commit()
        assert await SecurityRepository(session).latest_daily_bar(1) is None
        assert await SecurityRepository(session).latest_daily_bars_batch([1]) == {}
