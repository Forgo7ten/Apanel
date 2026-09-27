"""Track completed security-data bootstrap runs."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_bootstrap_ready"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "securities",
        sa.Column("bootstrap_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing securities predate the readiness contract. Mark them ready at
    # migration time so existing watch rows do not regress into a pending UI.
    op.execute(
        "UPDATE securities "
        "SET bootstrap_completed_at = CURRENT_TIMESTAMP "
        "WHERE bootstrap_completed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("securities", "bootstrap_completed_at")
