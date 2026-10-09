"""Reset optimistic bootstrap readiness from the original rollout.

Revision ID: 0003_reset_bootstrap_readiness
Revises: 0002_bootstrap_ready
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_reset_bootstrap_readiness"
down_revision: str | Sequence[str] | None = "0002_bootstrap_ready"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0002 marked every existing security ready without proving that its
    # required qfq/none history had ever been bootstrapped.  Reset the marker
    # conservatively.  The normal EOD readiness path will set it again only
    # after the current shared indicator demand has passed readiness/bootstrap.
    op.execute("UPDATE securities SET bootstrap_completed_at = NULL")


def downgrade() -> None:
    # The pre-fix timestamps were not trustworthy and cannot be reconstructed.
    # Leave readiness conservative rather than fabricating completion times.
    pass
