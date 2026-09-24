"""Create user-owned watch tables, dynamic columns and settings."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_sprint4_watch_tables_settings"
down_revision: str | Sequence[str] | None = "0004_sprint3_indicator_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Sprint 4 user-owned monitoring schema."""

    op.create_table(
        "watch_tables",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name", name="uq_watch_tables_user_name"),
    )
    op.create_index(
        "uq_watch_tables_user_name_ci",
        "watch_tables",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index("ix_watch_tables_user_id", "watch_tables", ["user_id"])

    op.create_table(
        "watch_table_symbols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("watch_table_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["watch_table_id"], ["watch_tables.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "watch_table_id",
            "security_id",
            name="uq_watch_table_symbols_table_security",
        ),
    )
    op.create_index(
        "ix_watch_table_symbols_table_position",
        "watch_table_symbols",
        ["watch_table_id", "position"],
    )
    op.create_index(
        "ix_watch_table_symbols_security_id", "watch_table_symbols", ["security_id"]
    )

    op.create_table(
        "table_columns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("watch_table_id", sa.Integer(), nullable=False),
        sa.Column("column_type", sa.String(length=16), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("view_mode", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("width", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["watch_table_id"], ["watch_tables.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_table_columns_watch_table_position",
        "table_columns",
        ["watch_table_id", "position"],
    )
    op.create_index(
        "ix_table_columns_watch_table_type",
        "table_columns",
        ["watch_table_id", "column_type"],
    )

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_settings_user_id"),
    )
    op.create_index("ix_user_settings_user_id", "user_settings", ["user_id"])


def downgrade() -> None:
    """Drop Sprint 4 tables in reverse dependency order."""

    op.drop_index("ix_user_settings_user_id", table_name="user_settings")
    op.drop_table("user_settings")
    op.drop_index("ix_table_columns_watch_table_type", table_name="table_columns")
    op.drop_index("ix_table_columns_watch_table_position", table_name="table_columns")
    op.drop_table("table_columns")
    op.drop_index("ix_watch_table_symbols_security_id", table_name="watch_table_symbols")
    op.drop_index(
        "ix_watch_table_symbols_table_position", table_name="watch_table_symbols"
    )
    op.drop_table("watch_table_symbols")
    op.drop_index("ix_watch_tables_user_id", table_name="watch_tables")
    op.drop_index("uq_watch_tables_user_name_ci", table_name="watch_tables")
    op.drop_table("watch_tables")
