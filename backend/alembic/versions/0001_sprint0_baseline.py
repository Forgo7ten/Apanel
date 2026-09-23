"""Establish the Sprint 0 migration head without business tables."""

from collections.abc import Sequence

revision: str = "0001_sprint0_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Sprint 0 deliberately has no business schema."""


def downgrade() -> None:
    """There is no Sprint 0 schema to remove."""
