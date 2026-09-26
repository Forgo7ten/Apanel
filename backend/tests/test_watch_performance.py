"""Offline query-count and broad execution coverage for watch-table detail."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from time import perf_counter

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import (
    DailyBar,
    IndicatorSnapshot,
    IndicatorState,
    QuoteSnapshot,
    Security,
    TableColumn,
    User,
    UserStatus,
    WatchTable,
    WatchTableSymbol,
)
from app.repositories.security import DIVIDEND_EVENTS_TABLE
from app.services.watch_table_service import WatchTableService


@pytest_asyncio.fixture
async def watch_performance_context(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'watch-performance.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            username="watch-performance",
            email="watch-performance@example.com",
            password_hash="not-used",
            status=UserStatus.ACTIVE,
        )
        small_table = WatchTable(user=user, name="一只股票")
        large_table = WatchTable(user=user, name="一百只股票")
        session.add_all([user, small_table, large_table])
        await session.flush()

        securities = [
            Security(
                symbol=f"{600000 + index:06d}",
                name=f"测试证券{index}",
                market="SH",
                exchange="SH",
                security_type="STOCK",
                status="ACTIVE",
            )
            for index in range(100)
        ]
        session.add_all(securities)
        await session.flush()

        session.add_all(
            [
                WatchTableSymbol(
                    watch_table_id=small_table.id,
                    security_id=securities[0].id,
                    position=0,
                )
            ]
            + [
                WatchTableSymbol(
                    watch_table_id=large_table.id,
                    security_id=security.id,
                    position=index,
                )
                for index, security in enumerate(securities)
            ]
        )
        for table in (small_table, large_table):
            session.add_all(
                [
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="RSI",
                        parameters={"period": 14},
                        view_mode="NUMBER",
                        position=0,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="MA",
                        parameters={"period": 5},
                        view_mode="NUMBER",
                        position=1,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="BOLL",
                        parameters={"period": 20, "multiplier": 2},
                        view_mode="COMPOSITE",
                        position=2,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="KDJ",
                        parameters={"period": 9, "k_period": 3, "d_period": 3},
                        view_mode="COMPOSITE",
                        position=3,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="MACD",
                        parameters={"fast_period": 12, "slow_period": 26, "signal_period": 9},
                        view_mode="COMPOSITE",
                        position=4,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="PROJECTED_MA",
                        parameters={"period": 5},
                        view_mode="NUMBER",
                        position=5,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="RSI",
                        parameters={"period": 6},
                        view_mode="NUMBER",
                        position=6,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="RSI",
                        parameters={"period": 21},
                        view_mode="NUMBER",
                        position=7,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="RSI",
                        parameters={"period": 28},
                        view_mode="NUMBER",
                        position=8,
                    ),
                    TableColumn(
                        watch_table_id=table.id,
                        column_type="INDICATOR",
                        indicator_type="DIVIDEND_YIELD",
                        parameters={},
                        view_mode="NUMBER",
                        position=9,
                    ),
                ]
            )

        now = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
        session.add_all(
            [
                QuoteSnapshot(
                    security_id=security.id,
                    price=Decimal("100"),
                    change=Decimal("1"),
                    change_percent=Decimal("1"),
                    timestamp=now,
                )
                for security in securities
                if security.id != securities[0].id
            ]
        )
        session.add(
            DailyBar(
                security_id=securities[0].id,
                trade_date=date(2026, 9, 23),
                open=Decimal("99"),
                high=Decimal("101"),
                low=Decimal("98"),
                close=Decimal("100"),
                volume=100,
                amount=10000,
                adjust_type="qfq",
            )
        )
        session.add_all(
            [
                IndicatorSnapshot(
                    security_id=security.id,
                    trade_date=date(2026, 9, 23),
                    indicator_type="RSI",
                    parameters={"period": 14},
                    values={"value": 71},
                    previous_values={"value": 69},
                    delta={"value": 2},
                )
                for security in securities
            ]
        )
        session.add_all(
            [
                IndicatorState(
                    security_id=security.id,
                    trade_date=date(2026, 9, 23),
                    state_code="RSI_OVERBOUGHT",
                    indicator_type="RSI",
                    status="ACTIVE",
                    metadata={"name": "RSI 高位", "level": "WARNING", "active": True},
                )
                for security in securities
            ]
        )
        await session.flush()
        await session.execute(
            DIVIDEND_EVENTS_TABLE.insert(),
            [
                {
                    "security_id": security.id,
                    "date": date(2026, 1, 1),
                    "cash_amount": Decimal("1"),
                }
                for security in securities
            ],
        )
        await session.commit()
        await session.refresh(user)
        await session.refresh(small_table)
        await session.refresh(large_table)
        table_ids = (small_table.id, large_table.id)
        user_id = user.id

    yield engine, session_factory, user_id, table_ids
    await engine.dispose()


async def test_watch_detail_query_count_is_independent_of_stock_count(
    watch_performance_context,
) -> None:
    engine, session_factory, user_id, (small_table_id, large_table_id) = watch_performance_context
    statements: list[str] = []

    def count_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        async with session_factory() as session:
            small = await WatchTableService(session).detail(user_id, small_table_id)
            small_count = len(statements)
            statements.clear()
            started = perf_counter()
            large = await WatchTableService(session).detail(user_id, large_table_id)
            elapsed = perf_counter() - started
            large_count = len(statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    print(f"watch_detail_100x10 query_count={large_count} elapsed={elapsed:.3f}s")
    assert len(small.stocks) == 1
    assert len(large.stocks) == 100
    assert small_count == large_count
    assert large_count <= 8
    assert elapsed < 10
