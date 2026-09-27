"""Expand analysis persistence with adjustment and parameter identity."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011a_analysis_expand"
down_revision: str | Sequence[str] | None = "0010_trading_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "indicator_snapshots",
        sa.Column("adjust_type", sa.String(length=8), nullable=False, server_default="qfq"),
    )
    op.create_unique_constraint(
        "uq_indicator_snapshots_security_date_type_adjust",
        "indicator_snapshots",
        ["security_id", "trade_date", "indicator_type", "adjust_type"],
    )
    op.create_index(
        "ix_indicator_snapshots_security_indicator_adjust_date",
        "indicator_snapshots",
        ["security_id", "indicator_type", "adjust_type", "trade_date"],
    )
    op.add_column(
        "indicator_states",
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "indicator_states",
        sa.Column("parameter_key", sa.String(length=67), nullable=False, server_default="default"),
    )
    op.add_column(
        "indicator_states",
        sa.Column("adjust_type", sa.String(length=8), nullable=False, server_default="qfq"),
    )
    op.create_unique_constraint(
        "uq_indicator_states_security_date_code_param_adjust",
        "indicator_states",
        ["security_id", "trade_date", "state_code", "parameter_key", "adjust_type"],
    )
    op.create_index(
        "ix_indicator_states_security_adjust_date",
        "indicator_states",
        ["security_id", "adjust_type", "trade_date"],
    )
    op.add_column("table_columns", sa.Column("state_code", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("table_columns", "state_code")
    op.drop_index("ix_indicator_states_security_adjust_date", table_name="indicator_states")
    op.drop_constraint(
        "uq_indicator_states_security_date_code_param_adjust", "indicator_states", type_="unique"
    )
    op.drop_column("indicator_states", "adjust_type")
    op.drop_column("indicator_states", "parameter_key")
    op.drop_column("indicator_states", "parameters")
    op.drop_index(
        "ix_indicator_snapshots_security_indicator_adjust_date", table_name="indicator_snapshots"
    )
    op.drop_constraint(
        "uq_indicator_snapshots_security_date_type_adjust", "indicator_snapshots", type_="unique"
    )
    op.drop_column("indicator_snapshots", "adjust_type")
