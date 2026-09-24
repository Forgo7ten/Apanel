"""Create user-owned alert rules, durable edge state and notifications."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_sprint5_alert_notifications"
down_revision: str | Sequence[str] | None = "0005_sprint4_watch_tables_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Sprint 5 alert and notification persistence."""

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("condition_type", sa.String(length=16), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=True),
        sa.Column("state_code", sa.String(length=64), nullable=True),
        sa.Column("operator", sa.String(length=4), nullable=True),
        sa.Column("threshold", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.CheckConstraint(
            "condition_type IN ('VALUE', 'STATE')",
            name="ck_alert_rules_condition_type",
        ),
        sa.CheckConstraint(
            "((condition_type = 'VALUE' AND indicator_type IS NOT NULL "
            "AND operator IS NOT NULL AND threshold IS NOT NULL AND state_code IS NULL) "
            "OR (condition_type = 'STATE' AND state_code IS NOT NULL "
            "AND indicator_type IS NULL AND operator IS NULL AND threshold IS NULL))",
            name="ck_alert_rules_condition_fields",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_alert_rules_user_id", "alert_rules", ["user_id"])
    op.create_index(
        "ix_alert_rules_user_security", "alert_rules", ["user_id", "security_id"]
    )
    op.create_index("ix_alert_rules_security_id", "alert_rules", ["security_id"])

    op.create_table(
        "alert_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alert_rule_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RESET"),
        sa.Column("last_trigger_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RESET')",
            name="ck_alert_instances_status",
        ),
        sa.ForeignKeyConstraint(
            ["alert_rule_id"], ["alert_rules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["security_id"], ["securities.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "alert_rule_id",
            "security_id",
            name="uq_alert_instances_rule_security",
        ),
    )
    op.create_index(
        "ix_alert_instances_rule_status", "alert_instances", ["alert_rule_id", "status"]
    )
    op.create_index("ix_alert_instances_security_id", "alert_instances", ["security_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("alert_rule_id", sa.Integer(), nullable=True),
        sa.Column("security_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="FEISHU"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "channel IN ('FEISHU', 'WECHAT', 'EMAIL')",
            name="ck_notifications_channel",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED')",
            name="ck_notifications_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["alert_rule_id"], ["alert_rules.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["security_id"], ["securities.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_notifications_user_created_at", "notifications", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_notifications_user_status", "notifications", ["user_id", "status"]
    )
    op.create_index("ix_notifications_alert_rule_id", "notifications", ["alert_rule_id"])


def downgrade() -> None:
    """Drop Sprint 5 tables in reverse dependency order."""

    op.drop_index("ix_notifications_alert_rule_id", table_name="notifications")
    op.drop_index("ix_notifications_user_status", table_name="notifications")
    op.drop_index("ix_notifications_user_created_at", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_alert_instances_security_id", table_name="alert_instances")
    op.drop_index("ix_alert_instances_rule_status", table_name="alert_instances")
    op.drop_table("alert_instances")
    op.drop_index("ix_alert_rules_security_id", table_name="alert_rules")
    op.drop_index("ix_alert_rules_user_security", table_name="alert_rules")
    op.drop_index("ix_alert_rules_user_id", table_name="alert_rules")
    op.drop_table("alert_rules")
