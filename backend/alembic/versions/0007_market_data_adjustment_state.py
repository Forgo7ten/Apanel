"""Add market-data provider adjustment revision state."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_adjustment_state"
down_revision: str | Sequence[str] | None = "0006_sprint5_alert_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_data_adjustment_state",
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=32), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column("qfq_coverage_start", sa.Date(), nullable=True),
        sa.Column("qfq_coverage_end", sa.Date(), nullable=True),
        sa.Column("last_rebased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("security_id"),
    )


def downgrade() -> None:
    op.drop_table("market_data_adjustment_state")
