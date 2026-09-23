"""Add invitation-based authentication and rotating refresh sessions."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_sprint1_auth"
down_revision: str | Sequence[str] | None = "0001_sprint0_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Sprint 1 auth schema."""

    user_role = sa.Enum("ADMIN", "USER", name="user_role")
    user_status = sa.Enum("INVITED", "ACTIVE", "DISABLED", name="user_status")
    invitation_status = sa.Enum(
        "PENDING", "ACCEPTED", "REVOKED", "EXPIRED", name="invitation_status"
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="USER"),
        sa.Column("status", user_status, nullable=False, server_default="INVITED"),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "uq_users_username_ci",
        "users",
        [sa.text("lower(username)")],
        unique=True,
    )
    op.create_index("uq_users_email_ci", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", invitation_status, nullable=False, server_default="PENDING"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("uq_invitations_token_hash", "invitations", ["token_hash"], unique=True)

    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_refresh_sessions_token_hash",
        "refresh_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_refresh_sessions_family_id", "refresh_sessions", ["family_id"])


def downgrade() -> None:
    """Remove the Sprint 1 auth schema."""

    op.drop_index("ix_refresh_sessions_family_id", table_name="refresh_sessions")
    op.drop_index("uq_refresh_sessions_token_hash", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_index("uq_invitations_token_hash", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("uq_users_email_ci", table_name="users")
    op.drop_index("uq_users_username_ci", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    sa.Enum("PENDING", "ACCEPTED", "REVOKED", "EXPIRED", name="invitation_status").drop(
        bind, checkfirst=True
    )
    sa.Enum("INVITED", "ACTIVE", "DISABLED", name="user_status").drop(bind, checkfirst=True)
    sa.Enum("ADMIN", "USER", name="user_role").drop(bind, checkfirst=True)
