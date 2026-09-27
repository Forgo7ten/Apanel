"""Add persisted trading-calendar expectations and validation state."""

from collections.abc import Sequence
from datetime import date, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "0010_trading_calendar"
down_revision: str | Sequence[str] | None = "0009_alert_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_calendar",
        sa.Column("market", sa.String(length=8), nullable=False, server_default="CN"),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("expected_open", sa.Boolean(), nullable=True),
        sa.Column("actual_open", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="UNKNOWN"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CLOSED', 'UNKNOWN')", name="ck_trading_calendar_status"
        ),
        sa.PrimaryKeyConstraint("market", "trade_date"),
    )
    table = sa.table(
        "trading_calendar",
        sa.column("market", sa.String()),
        sa.column("trade_date", sa.Date()),
        sa.column("expected_open", sa.Boolean()),
        sa.column("actual_open", sa.Boolean()),
        sa.column("status", sa.String()),
        sa.column("source", sa.String()),
        sa.column("source_metadata", sa.JSON()),
    )
    rows = []
    closures = {
        2025: (
            (date(2025, 1, 1), date(2025, 1, 1)),
            (date(2025, 1, 28), date(2025, 2, 4)),
            (date(2025, 4, 4), date(2025, 4, 6)),
            (date(2025, 5, 1), date(2025, 5, 5)),
            (date(2025, 5, 31), date(2025, 6, 2)),
            (date(2025, 10, 1), date(2025, 10, 8)),
        ),
        2026: (
            (date(2026, 1, 1), date(2026, 1, 3)),
            (date(2026, 2, 15), date(2026, 2, 23)),
            (date(2026, 4, 4), date(2026, 4, 6)),
            (date(2026, 5, 1), date(2026, 5, 5)),
            (date(2026, 6, 19), date(2026, 6, 21)),
            (date(2026, 9, 25), date(2026, 9, 27)),
            (date(2026, 10, 1), date(2026, 10, 7)),
        ),
    }
    source_urls = {
        2025: "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml",
        2026: "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
    }
    for year in (2025, 2026):
        current = date(year, 1, 1)
        end = date(year, 12, 31)
        while current <= end:
            closed = current.weekday() >= 5 or any(
                start <= current <= stop for start, stop in closures[year]
            )
            rows.append(
                {
                    "market": "CN",
                    "trade_date": current,
                    "expected_open": not closed,
                    "actual_open": None,
                    "status": "CLOSED" if closed else "OPEN",
                    "source": "SSE_ANNUAL",
                    "source_metadata": {"year": year, "url": source_urls[year]},
                }
            )
            current += timedelta(days=1)
    op.bulk_insert(table, rows)


def downgrade() -> None:
    op.drop_table("trading_calendar")
