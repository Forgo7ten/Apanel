"""Create the shared Sprint 2 market-data schema."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_sprint2_market_data"
down_revision: str | Sequence[str] | None = "0002_sprint1_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the tables persisted by Market Data Service."""

    op.create_table(
        "securities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=6), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("exchange", sa.String(length=2), nullable=False),
        sa.Column("security_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("high", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("low", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("close", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("volume", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("amount", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("adjust_type", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "adjust_type",
            name="uq_daily_bars_security_date_adjust",
        ),
    )
    op.create_index(
        "ix_daily_bars_security_trade_date",
        "daily_bars",
        ["security_id", "trade_date"],
    )

    op.create_table(
        "quote_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("change", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("change_percent", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "timestamp",
            name="uq_quote_snapshots_security_timestamp",
        ),
    )
    op.create_index(
        "ix_quote_snapshots_security_timestamp",
        "quote_snapshots",
        ["security_id", "timestamp"],
    )

    op.create_table(
        "dividend_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("cash_amount", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "date",
            name="uq_dividend_events_security_date",
        ),
    )
    op.create_index(
        "ix_dividend_events_security_date",
        "dividend_events",
        ["security_id", "date"],
    )


def downgrade() -> None:
    """Remove Sprint 2 tables in reverse dependency order."""

    op.drop_index("ix_dividend_events_security_date", table_name="dividend_events")
    op.drop_table("dividend_events")
    op.drop_index("ix_quote_snapshots_security_timestamp", table_name="quote_snapshots")
    op.drop_table("quote_snapshots")
    op.drop_index("ix_daily_bars_security_trade_date", table_name="daily_bars")
    op.drop_table("daily_bars")
    op.drop_table("securities")
