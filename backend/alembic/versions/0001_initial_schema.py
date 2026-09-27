"""Create the complete Apanel v1 schema from an empty database.

This baseline replaces the pre-release migration history. It is intentionally
self-contained so future migrations can evolve from a stable schema snapshot
without importing live ORM models.
"""

from collections.abc import Sequence
from datetime import date, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the current Apanel schema and seed the trading calendar."""

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
    op.create_index(
        "uq_users_email_ci",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

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
    op.create_index(
        "uq_invitations_token_hash",
        "invitations",
        ["token_hash"],
        unique=True,
    )

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
    op.create_index(
        "ix_refresh_sessions_family_id",
        "refresh_sessions",
        ["family_id"],
    )

    op.create_table(
        "securities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=6), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("exchange", sa.String(length=2), nullable=False),
        sa.Column("security_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("high", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("low", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("close", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("volume", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("amount", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("adjust_type", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "adjust_type",
            name="uq_daily_bars_security_date_adjust",
        ),
    )
    op.create_index(
        "ix_daily_bars_security_trade_date",
        "daily_bars",
        ["security_id", "trade_date"],
    )

    op.create_table(
        "quote_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("change", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("change_percent", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "timestamp",
            name="uq_quote_snapshots_security_timestamp",
        ),
    )
    op.create_index(
        "ix_quote_snapshots_security_timestamp",
        "quote_snapshots",
        ["security_id", "timestamp"],
    )

    op.create_table(
        "dividend_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("cash_amount", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "date",
            name="uq_dividend_events_security_date",
        ),
    )
    op.create_index(
        "ix_dividend_events_security_date",
        "dividend_events",
        ["security_id", "date"],
    )

    op.create_table(
        "market_data_adjustment_state",
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=32), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column("qfq_coverage_start", sa.Date(), nullable=True),
        sa.Column("qfq_coverage_end", sa.Date(), nullable=True),
        sa.Column("last_rebased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("security_id"),
    )

    op.create_table(
        "indicator_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=False),
        sa.Column(
            "adjust_type",
            sa.String(length=8),
            nullable=False,
            server_default="qfq",
        ),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("previous_values", sa.JSON(), nullable=True),
        sa.Column("delta", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "indicator_type",
            "adjust_type",
            name="uq_indicator_snapshots_security_date_type_adjust",
        ),
    )
    op.create_index(
        "ix_indicator_snapshots_security_indicator_adjust_date",
        "indicator_snapshots",
        ["security_id", "indicator_type", "adjust_type", "trade_date"],
    )

    op.create_table(
        "state_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("state_code", sa.String(length=64), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "level",
            sa.String(length=16),
            nullable=False,
            server_default="INFO",
        ),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "state_code",
            name="uq_state_definitions_code",
        ),
    )

    op.create_table(
        "indicator_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "security_id",
            sa.Integer(),
            sa.ForeignKey("securities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("state_code", sa.String(length=64), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=False),
        sa.Column(
            "parameters",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "parameter_key",
            sa.String(length=67),
            nullable=False,
            server_default="default",
        ),
        sa.Column(
            "adjust_type",
            sa.String(length=8),
            nullable=False,
            server_default="qfq",
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "security_id",
            "trade_date",
            "state_code",
            "parameter_key",
            "adjust_type",
            name="uq_indicator_states_security_date_code_param_adjust",
        ),
    )
    op.create_index(
        "ix_indicator_states_security_adjust_date",
        "indicator_states",
        ["security_id", "adjust_type", "trade_date"],
    )

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
        sa.UniqueConstraint(
            "user_id",
            "name",
            name="uq_watch_tables_user_name",
        ),
    )
    op.create_index(
        "uq_watch_tables_user_name_ci",
        "watch_tables",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        "ix_watch_tables_user_id",
        "watch_tables",
        ["user_id"],
    )

    op.create_table(
        "watch_table_symbols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("watch_table_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["watch_table_id"],
            ["watch_tables.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="CASCADE",
        ),
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
        "ix_watch_table_symbols_security_id",
        "watch_table_symbols",
        ["security_id"],
    )

    op.create_table(
        "table_columns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("watch_table_id", sa.Integer(), nullable=False),
        sa.Column("column_type", sa.String(length=16), nullable=False),
        sa.Column("indicator_type", sa.String(length=32), nullable=True),
        sa.Column("state_code", sa.String(length=64), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("view_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
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
            ["watch_table_id"],
            ["watch_tables.id"],
            ondelete="CASCADE",
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
        sa.UniqueConstraint(
            "user_id",
            name="uq_user_settings_user_id",
        ),
    )
    op.create_index(
        "ix_user_settings_user_id",
        "user_settings",
        ["user_id"],
    )

    op.create_table(
        "user_secrets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("secret_type", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=False),
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
        sa.UniqueConstraint(
            "user_id",
            "secret_type",
            name="uq_user_secrets_user_type",
        ),
    )
    op.create_index(
        "ix_user_secrets_user_id",
        "user_secrets",
        ["user_id"],
    )

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
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("parameter_key", sa.String(length=67), nullable=True),
        sa.Column("field", sa.String(length=64), nullable=True),
        sa.Column(
            "adjust_type",
            sa.String(length=8),
            nullable=False,
            server_default="qfq",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
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
            "AND operator IS NULL AND threshold IS NULL))",
            name="ck_alert_rules_condition_fields",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_alert_rules_user_id",
        "alert_rules",
        ["user_id"],
    )
    op.create_index(
        "ix_alert_rules_user_security",
        "alert_rules",
        ["user_id", "security_id"],
    )
    op.create_index(
        "ix_alert_rules_security_id",
        "alert_rules",
        ["security_id"],
    )

    op.create_table(
        "alert_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("alert_rule_id", sa.Integer(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="RESET",
        ),
        sa.Column("last_trigger_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "trigger_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
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
            ["alert_rule_id"],
            ["alert_rules.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "alert_rule_id",
            "security_id",
            name="uq_alert_instances_rule_security",
        ),
    )
    op.create_index(
        "ix_alert_instances_rule_status",
        "alert_instances",
        ["alert_rule_id", "status"],
    )
    op.create_index(
        "ix_alert_instances_security_id",
        "alert_instances",
        ["security_id"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("alert_rule_id", sa.Integer(), nullable=True),
        sa.Column("security_id", sa.Integer(), nullable=True),
        sa.Column(
            "channel",
            sa.String(length=16),
            nullable=False,
            server_default="FEISHU",
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_sequence", sa.Integer(), nullable=True),
        sa.Column("observation_date", sa.Date(), nullable=True),
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
            ["alert_rule_id"],
            ["alert_rules.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "alert_rule_id",
            "security_id",
            "trigger_sequence",
            name="uq_notifications_rule_security_sequence",
        ),
    )
    op.create_index(
        "ix_notifications_user_created_at",
        "notifications",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_notifications_user_status",
        "notifications",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_notifications_alert_rule_id",
        "notifications",
        ["alert_rule_id"],
    )

    op.create_table(
        "trading_calendar",
        sa.Column(
            "market",
            sa.String(length=8),
            nullable=False,
            server_default="CN",
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("expected_open", sa.Boolean(), nullable=True),
        sa.Column("actual_open", sa.Boolean(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "source_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CLOSED', 'UNKNOWN')",
            name="ck_trading_calendar_status",
        ),
        sa.PrimaryKeyConstraint("market", "trade_date"),
    )
    _seed_trading_calendar()


def _seed_trading_calendar() -> None:
    table = sa.table(
        "trading_calendar",
        sa.column("market", sa.String()),
        sa.column("trade_date", sa.Date()),
        sa.column("expected_open", sa.Boolean()),
        sa.column("actual_open", sa.Boolean()),
        sa.column("status", sa.String()),
        sa.column("source", sa.String()),
        sa.column("source_metadata", sa.JSON()),
    )
    closures = {
        2025: (
            (date(2025, 1, 1), date(2025, 1, 1)),
            (date(2025, 1, 28), date(2025, 2, 4)),
            (date(2025, 4, 4), date(2025, 4, 6)),
            (date(2025, 5, 1), date(2025, 5, 5)),
            (date(2025, 5, 31), date(2025, 6, 2)),
            (date(2025, 10, 1), date(2025, 10, 8)),
        ),
        2026: (
            (date(2026, 1, 1), date(2026, 1, 3)),
            (date(2026, 2, 15), date(2026, 2, 23)),
            (date(2026, 4, 4), date(2026, 4, 6)),
            (date(2026, 5, 1), date(2026, 5, 5)),
            (date(2026, 6, 19), date(2026, 6, 21)),
            (date(2026, 9, 25), date(2026, 9, 27)),
            (date(2026, 10, 1), date(2026, 10, 7)),
        ),
    }
    source_urls = {
        2025: "https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml",
        2026: "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
    }
    rows = []
    for year in (2025, 2026):
        current = date(year, 1, 1)
        end = date(year, 12, 31)
        while current <= end:
            closed = current.weekday() >= 5 or any(
                start <= current <= stop for start, stop in closures[year]
            )
            rows.append(
                {
                    "market": "CN",
                    "trade_date": current,
                    "expected_open": not closed,
                    "actual_open": None,
                    "status": "CLOSED" if closed else "OPEN",
                    "source": "SSE_ANNUAL",
                    "source_metadata": {
                        "year": year,
                        "url": source_urls[year],
                    },
                }
            )
            current += timedelta(days=1)
    op.bulk_insert(table, rows)


def downgrade() -> None:
    """Drop the complete Apanel schema."""

    for table_name in (
        "trading_calendar",
        "notifications",
        "alert_instances",
        "alert_rules",
        "user_secrets",
        "user_settings",
        "table_columns",
        "watch_table_symbols",
        "watch_tables",
        "indicator_states",
        "state_definitions",
        "indicator_snapshots",
        "market_data_adjustment_state",
        "dividend_events",
        "quote_snapshots",
        "daily_bars",
        "refresh_sessions",
        "invitations",
        "securities",
        "users",
    ):
        op.drop_table(table_name)

    bind = op.get_bind()
    sa.Enum(
        "PENDING",
        "ACCEPTED",
        "REVOKED",
        "EXPIRED",
        name="invitation_status",
    ).drop(bind, checkfirst=True)
    sa.Enum(
        "INVITED",
        "ACTIVE",
        "DISABLED",
        name="user_status",
    ).drop(bind, checkfirst=True)
    sa.Enum(
        "ADMIN",
        "USER",
        name="user_role",
    ).drop(bind, checkfirst=True)
