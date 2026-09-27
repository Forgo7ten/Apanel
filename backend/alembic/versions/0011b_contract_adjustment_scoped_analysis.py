"""Contract legacy analysis identity after bridge deployment."""

from collections.abc import Sequence

from alembic import op

revision: str = "0011b_analysis_contract"
down_revision: str | Sequence[str] | None = "0011a_analysis_expand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_indicator_snapshots_security_date_type", "indicator_snapshots", type_="unique"
    )
    op.drop_constraint("uq_indicator_states_security_date_code", "indicator_states", type_="unique")
    op.drop_index(
        "ix_indicator_snapshots_security_indicator_date", table_name="indicator_snapshots"
    )
    op.drop_index("ix_indicator_states_security_trade_date", table_name="indicator_states")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_indicator_snapshots_security_date_type",
        "indicator_snapshots",
        ["security_id", "trade_date", "indicator_type"],
    )
    op.create_index(
        "ix_indicator_snapshots_security_indicator_date",
        "indicator_snapshots",
        ["security_id", "indicator_type", "trade_date"],
    )
    op.create_unique_constraint(
        "uq_indicator_states_security_date_code",
        "indicator_states",
        ["security_id", "trade_date", "state_code"],
    )
    op.create_index(
        "ix_indicator_states_security_trade_date",
        "indicator_states",
        ["security_id", "trade_date"],
    )
