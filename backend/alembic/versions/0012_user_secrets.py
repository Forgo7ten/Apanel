"""Add encrypted per-user secret storage."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_user_secrets"
down_revision: str | Sequence[str] | None = "0011b_analysis_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_secrets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("secret_type", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "secret_type", name="uq_user_secrets_user_type"),
    )
    op.create_index("ix_user_secrets_user_id", "user_secrets", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_secrets_user_id", table_name="user_secrets")
    op.drop_table("user_secrets")
