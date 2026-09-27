"""Make alert edge claims and notification outbox rows share one transaction."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_alert_outbox"
down_revision: str | Sequence[str] | None = "0008_alert_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert_instances",
        sa.Column("trigger_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("notifications", sa.Column("trigger_sequence", sa.Integer(), nullable=True))
    op.add_column("notifications", sa.Column("observation_date", sa.Date(), nullable=True))
    op.create_unique_constraint(
        "uq_notifications_rule_security_sequence",
        "notifications",
        ["alert_rule_id", "security_id", "trigger_sequence"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_notifications_rule_security_sequence", "notifications", type_="unique")
    op.drop_column("notifications", "observation_date")
    op.drop_column("notifications", "trigger_sequence")
    op.drop_column("alert_instances", "trigger_sequence")
