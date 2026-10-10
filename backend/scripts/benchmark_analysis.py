"""Reproducible DATABASE/COMPUTE benchmark, without market providers or Webhook sends.

Run from backend: APP_ENV=test APANEL_BENCHMARK_POSTGRES_URL=... \
    python scripts/benchmark_analysis.py --symbols 100 --bars 400 --output report.json
Only an EMPTY PostgreSQL database named apanel_bench_* (or apanel_remaining_bench*)
is accepted. Existing tables are never dropped. Times exclude fixture seeding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import resource
import statistics
import sys
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event, func, insert, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402
from app.models import (  # noqa: E402
    AnalysisStream,
    CurrentIndicatorState,
    DailyBar,
    IndicatorSnapshot,
    Security,
    StateEvent,
    TradingCalendar,
)
from app.services.analysis_control import lock_analysis_stream  # noqa: E402
from app.services.analysis_service import AnalysisService  # noqa: E402


def bar_payload(sid, mode, day, offset):
    close = Decimal(str(round(100 + sid % 10 + offset * 0.03 + 8 * math.sin(offset / 7), 6)))
    if mode == "qfq":
        close *= Decimal("0.8")
    return dict(
        security_id=sid,
        adjust_type=mode,
        trade_date=day,
        open=close,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=1000,
        amount=close * 1000,
    )


async def benchmark(args):
    raw_url = os.environ.get("APANEL_BENCHMARK_POSTGRES_URL", "")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        ("apanel_bench_", "apanel_remaining_bench")
    ):
        raise ValueError("Use a dedicated empty apanel_bench_* PostgreSQL database")
    engine = create_async_engine(url, pool_size=5, connect_args={"command_timeout": 60})
    try:
        async with engine.begin() as connection:
            tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            if tables:
                raise ValueError("Benchmark refuses a database with existing tables")
            await connection.run_sync(Base.metadata.create_all)
            version = (await connection.execute(text("SELECT version()"))).scalar_one()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        days = []
        calendar = []
        current = date(2024, 1, 1)
        while len(days) < args.bars + 1:
            opened = current.weekday() < 5
            if opened:
                days.append(current)
            calendar.append(
                dict(
                    market="CN",
                    trade_date=current,
                    status="OPEN" if opened else "CLOSED",
                    expected_open=opened,
                    actual_open=opened,
                    source="BENCH_SYNTHETIC",
                    source_metadata={},
                )
            )
            current += timedelta(days=1)
        securities = [
            dict(
                id=i,
                symbol=f"{600000 + i:06d}",
                name=f"Bench {i}",
                market="SH",
                exchange="SH",
                security_type="STOCK",
                status="ACTIVE",
            )
            for i in range(1, args.symbols + 1)
        ]
        targets = [(row, mode) for row in securities for mode in ("qfq", "none")]
        async with factory() as session:
            await session.execute(insert(Security), securities)
            await session.execute(insert(TradingCalendar), calendar)
            await session.execute(
                insert(AnalysisStream),
                [
                    dict(
                        security_id=row["id"],
                        adjust_type=mode,
                        source_revision=1,
                        dirty_from=days[0],
                        latest_bar_date=days[args.bars - 1],
                        history_depth=args.bars,
                    )
                    for row, mode in targets
                ],
            )
            for row, mode in targets:
                await session.execute(
                    insert(DailyBar),
                    [
                        bar_payload(row["id"], mode, day, i)
                        for i, day in enumerate(days[: args.bars])
                    ],
                )
            await session.commit()
        statements = 0

        def count_sql(*_):
            nonlocal statements
            statements += 1

        event.listen(engine.sync_engine, "before_cursor_execute", count_sql)

        async def run_phase(label, through):
            nonlocal statements
            statements = 0
            start = time.perf_counter()
            latencies, indicator_rows, state_observations = [], 0, 0
            for index, (security, mode) in enumerate(targets, 1):
                tick = time.perf_counter()
                async with factory() as session:
                    result = await AnalysisService(session).materialize(
                        security["symbol"], adjustment=mode, end=through
                    )
                latencies.append(time.perf_counter() - tick)
                indicator_rows += result.indicators
                state_observations += result.states
                if index % 20 == 0:
                    print(f"{label}: {index}/{len(targets)} streams", flush=True)
            elapsed = time.perf_counter() - start
            sql_count = statements
            async with factory() as session:
                counts = {
                    model.__tablename__: (
                        await session.execute(select(func.count()).select_from(model))
                    ).scalar_one()
                    for model in (
                        DailyBar,
                        IndicatorSnapshot,
                        CurrentIndicatorState,
                        StateEvent,
                    )
                }
            assert "indicator_states" not in Base.metadata.tables
            assert counts["current_indicator_states"] == 18 * len(targets)
            result = dict(
                seconds=round(elapsed, 3),
                sql_statements=sql_count,
                per_stream_p50_ms=round(statistics.median(latencies) * 1000, 2),
                per_stream_p95_ms=round(
                    sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1] * 1000, 2
                ),
                indicator_rows_written=indicator_rows,
                transient_state_observations=state_observations,
                persisted_rows=counts,
                peak_python_rss_mib=round(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
                ),
            )
            print(json.dumps({label: result}), flush=True)
            return result

        cold = await run_phase("cold", days[args.bars - 1])
        async with factory() as session:
            for security, mode in targets:
                stream = await lock_analysis_stream(session, security["id"], mode)
                await session.execute(
                    insert(DailyBar), [bar_payload(security["id"], mode, days[-1], args.bars)]
                )
                stream.source_revision += 1
                stream.dirty_from = stream.latest_bar_date = days[-1]
            await session.commit()
        hot = await run_phase("hot_next_day", days[-1])
        repeat = await run_phase("idempotent_repeat", days[-1])
        assert repeat["indicator_rows_written"] == repeat["transient_state_observations"] == 0
        report = dict(
            scope=(
                "Synthetic completed OHLC + PostgreSQL + AnalysisService only; "
                "no provider/network/Alert/Feishu"
            ),
            symbols=args.symbols,
            history_bars=args.bars,
            adjustments=["qfq", "none"],
            python=platform.python_version(),
            postgres=version,
            cpu_count=os.cpu_count(),
            cold=cold,
            hot_next_day=hot,
            idempotent_repeat=repeat,
        )
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        return report
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=100)
    parser.add_argument("--bars", type=int, default=400)
    parser.add_argument("--output", default="analysis-benchmark.json")
    args = parser.parse_args()
    if not 1 <= args.symbols <= 100 or not 40 <= args.bars <= 10000:
        parser.error("symbols must be 1..100 and bars must be 40..10000")
    asyncio.run(benchmark(args))
