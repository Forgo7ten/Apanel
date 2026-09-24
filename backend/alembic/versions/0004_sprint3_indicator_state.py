"""Create durable indicator snapshots and state observations."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_sprint3_indicator_state"
down_revision: str | Sequence[str] | None = "0003_sprint2_market_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the shared indicator and state persistence tables."""

    op.create_table(
        "indicator_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("previous_values", sa.JSON(), nullable=True),
        sa.Column("delta", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "indicator_type",
            name="uq_indicator_snapshots_security_date_type",
        ),
    )
    op.create_index(
        "ix_indicator_snapshots_security_indicator_date",
        "indicator_snapshots",
        ["security_id", "indicator_type", "trade_date"],
    )

    op.create_table(
        "state_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("state_code", sa.String(length=64), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="INFO"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("state_code", name="uq_state_definitions_code"),
    )

    op.create_table(
        "indicator_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("state_code", sa.String(length=64), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "state_code",
            name="uq_indicator_states_security_date_code",
        ),
    )
    op.create_index(
        "ix_indicator_states_security_trade_date",
        "indicator_states",
        ["security_id", "trade_date"],
    )


def downgrade() -> None:
    """Drop indicator/state tables in reverse dependency order."""

    op.drop_index("ix_indicator_states_security_trade_date", table_name="indicator_states")
    op.drop_table("indicator_states")
    op.drop_table("state_definitions")
    op.drop_index(
        "ix_indicator_snapshots_security_indicator_date",
        table_name="indicator_snapshots",
    )
    op.drop_table("indicator_snapshots")
