"""SQLAlchemy models used by the modular backend."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(StrEnum):
    """Roles are a database-backed authorization attribute."""

    ADMIN = "ADMIN"
    USER = "USER"


class UserStatus(StrEnum):
    """Lifecycle status for user accounts."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class InvitationStatus(StrEnum):
    """Lifecycle status for one-time invitations."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AlertConditionType(StrEnum):
    """Persisted alert condition kinds."""

    VALUE = "VALUE"
    STATE = "STATE"


class AlertInstanceStatus(StrEnum):
    """Durable Edge Trigger state for one alert/security pair."""

    ACTIVE = "ACTIVE"
    RESET = "RESET"


class NotificationChannel(StrEnum):
    """Notification channels supported by the persistence contract."""

    FEISHU = "FEISHU"
    WECHAT = "WECHAT"
    EMAIL = "EMAIL"


class NotificationStatus(StrEnum):
    """Delivery lifecycle persisted for every notification attempt."""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Security(Base):
    """A security record shared with the market-data service."""

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(6), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    exchange: Mapped[str] = mapped_column(String(2), nullable=False)
    security_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    daily_bars: Mapped[list[DailyBar]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    quote_snapshots: Mapped[list[QuoteSnapshot]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    indicator_snapshots: Mapped[list[IndicatorSnapshot]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    indicator_states: Mapped[list[IndicatorState]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    watch_table_symbols: Mapped[list[WatchTableSymbol]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    alert_instances: Mapped[list[AlertInstance]] = relationship(
        back_populates="security", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(back_populates="security")


class DailyBar(Base):
    """One persisted daily OHLC bar from the shared market-data schema."""

    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "trade_date",
            "adjust_type",
            name="uq_daily_bars_security_date_adjust",
        ),
        Index("ix_daily_bars_security_trade_date", "security_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Any] = mapped_column(Numeric(asdecimal=True), nullable=False)
    high: Mapped[Any] = mapped_column(Numeric(asdecimal=True), nullable=False)
    low: Mapped[Any] = mapped_column(Numeric(asdecimal=True), nullable=False)
    close: Mapped[Any] = mapped_column(Numeric(asdecimal=True), nullable=False)
    volume: Mapped[Any] = mapped_column(Numeric(asdecimal=True), nullable=False)
    amount: Mapped[Any | None] = mapped_column(Numeric(asdecimal=True), nullable=True)
    adjust_type: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    security: Mapped[Security] = relationship(back_populates="daily_bars")


class QuoteSnapshot(Base):
    """One intraday quote shared by all users."""

    __tablename__ = "quote_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "timestamp",
            name="uq_quote_snapshots_security_timestamp",
        ),
        Index("ix_quote_snapshots_security_timestamp", "security_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Any] = mapped_column(Numeric(asdecimal=True), nullable=False)
    change: Mapped[Any | None] = mapped_column(Numeric(asdecimal=True), nullable=True)
    change_percent: Mapped[Any | None] = mapped_column(Numeric(asdecimal=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    security: Mapped[Security] = relationship(back_populates="quote_snapshots")


class IndicatorSnapshot(Base):
    """Durable indicator values for one security, day and indicator type."""

    __tablename__ = "indicator_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "trade_date",
            "indicator_type",
            name="uq_indicator_snapshots_security_date_type",
        ),
        Index(
            "ix_indicator_snapshots_security_indicator_date",
            "security_id",
            "indicator_type",
            "trade_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    indicator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    previous_values: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    delta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    security: Mapped[Security] = relationship(back_populates="indicator_snapshots")


class StateDefinition(Base):
    """Persisted public metadata for one registered indicator state."""

    __tablename__ = "state_definitions"
    __table_args__ = (UniqueConstraint("state_code", name="uq_state_definitions_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state_code: Mapped[str] = mapped_column(String(64), nullable=False)
    indicator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __init__(self, **kwargs: Any) -> None:
        # SQLAlchemy reserves ``metadata`` on declarative classes.  Keep the
        # database column name/document vocabulary while exposing a friendly
        # constructor alias for callers that build the domain projection.
        if "metadata" in kwargs:
            kwargs["metadata_json"] = kwargs.pop("metadata")
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattribute__(self, name: str) -> Any:
        if name == "metadata":
            return object.__getattribute__(self, "metadata_json")
        return super().__getattribute__(name)


class IndicatorState(Base):
    """One durable state observation, including inactive days for history."""

    __tablename__ = "indicator_states"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "trade_date",
            "state_code",
            name="uq_indicator_states_security_date_code",
        ),
        Index("ix_indicator_states_security_trade_date", "security_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    state_code: Mapped[str] = mapped_column(String(64), nullable=False)
    indicator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    security: Mapped[Security] = relationship(back_populates="indicator_states")

    def __init__(self, **kwargs: Any) -> None:
        if "metadata" in kwargs:
            kwargs["metadata_json"] = kwargs.pop("metadata")
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattribute__(self, name: str) -> Any:
        if name == "metadata":
            return object.__getattribute__(self, "metadata_json")
        return super().__getattribute__(name)


class User(Base):
    """An authenticated Apanel account."""

    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_username_ci", text("lower(username)"), unique=True),
        Index("uq_users_email_ci", text("lower(email)"), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False, default=UserRole.USER
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status"), nullable=False, default=UserStatus.INVITED
    )
    invited_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    watch_tables: Mapped[list[WatchTable]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    settings: Mapped[UserSetting | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Invitation(Base):
    """A one-time, hashed invitation token."""

    __tablename__ = "invitations"
    __table_args__ = (Index("uq_invitations_token_hash", "token_hash", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        SAEnum(InvitationStatus, name="invitation_status"),
        nullable=False,
        default=InvitationStatus.PENDING,
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshSession(Base):
    """Rotating opaque refresh-token state; only token hashes are persisted."""

    __tablename__ = "refresh_sessions"
    __table_args__ = (
        Index("uq_refresh_sessions_token_hash", "token_hash", unique=True),
        Index("ix_refresh_sessions_family_id", "family_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WatchTable(Base):
    """A user-owned monitoring table."""

    __tablename__ = "watch_tables"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_watch_tables_user_name"),
        Index("uq_watch_tables_user_name_ci", "user_id", text("lower(name)"), unique=True),
        Index("ix_watch_tables_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user: Mapped[User] = relationship(back_populates="watch_tables")
    symbols: Mapped[list[WatchTableSymbol]] = relationship(
        back_populates="watch_table",
        cascade="all, delete-orphan",
        order_by="WatchTableSymbol.position",
    )
    columns: Mapped[list[TableColumn]] = relationship(
        back_populates="watch_table", cascade="all, delete-orphan", order_by="TableColumn.position"
    )


class WatchTableSymbol(Base):
    """A security and its user-defined position within a monitoring table."""

    __tablename__ = "watch_table_symbols"
    __table_args__ = (
        UniqueConstraint(
            "watch_table_id",
            "security_id",
            name="uq_watch_table_symbols_table_security",
        ),
        Index("ix_watch_table_symbols_table_position", "watch_table_id", "position"),
        Index("ix_watch_table_symbols_security_id", "security_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_table_id: Mapped[int] = mapped_column(
        ForeignKey("watch_tables.id", ondelete="CASCADE"), nullable=False
    )
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    watch_table: Mapped[WatchTable] = relationship(back_populates="symbols")
    security: Mapped[Security] = relationship(back_populates="watch_table_symbols")


class TableColumn(Base):
    """A dynamic, user-owned display column for a monitoring table."""

    __tablename__ = "table_columns"
    __table_args__ = (
        Index("ix_table_columns_watch_table_position", "watch_table_id", "position"),
        Index("ix_table_columns_watch_table_type", "watch_table_id", "column_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_table_id: Mapped[int] = mapped_column(
        ForeignKey("watch_tables.id", ondelete="CASCADE"), nullable=False
    )
    column_type: Mapped[str] = mapped_column(String(16), nullable=False)
    indicator_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    view_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    watch_table: Mapped[WatchTable] = relationship(back_populates="columns")


class UserSetting(Base):
    """Per-user JSON settings; the owning user is the isolation boundary."""

    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_settings_user_id"),
        Index("ix_user_settings_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user: Mapped[User] = relationship(back_populates="settings")


class AlertRule(Base):
    """A user-owned rule evaluated against one shared security."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        CheckConstraint(
            "condition_type IN ('VALUE', 'STATE')",
            name="ck_alert_rules_condition_type",
        ),
        CheckConstraint(
            "((condition_type = 'VALUE' AND indicator_type IS NOT NULL "
            "AND operator IS NOT NULL AND threshold IS NOT NULL AND state_code IS NULL) "
            "OR (condition_type = 'STATE' AND state_code IS NOT NULL "
            "AND indicator_type IS NULL AND operator IS NULL AND threshold IS NULL))",
            name="ck_alert_rules_condition_fields",
        ),
        Index("ix_alert_rules_user_id", "user_id"),
        Index("ix_alert_rules_user_security", "user_id", "security_id"),
        Index("ix_alert_rules_security_id", "security_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    condition_type: Mapped[str] = mapped_column(String(16), nullable=False)
    indicator_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator: Mapped[str | None] = mapped_column(String(4), nullable=True)
    threshold: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user: Mapped[User] = relationship(back_populates="alert_rules")
    security: Mapped[Security] = relationship(back_populates="alert_rules")
    instances: Mapped[list[AlertInstance]] = relationship(
        back_populates="alert_rule", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(back_populates="alert_rule")


class AlertInstance(Base):
    """The persisted state used to implement restart-safe Edge Trigger logic."""

    __tablename__ = "alert_instances"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'RESET')",
            name="ck_alert_instances_status",
        ),
        UniqueConstraint(
            "alert_rule_id",
            "security_id",
            name="uq_alert_instances_rule_security",
        ),
        Index("ix_alert_instances_rule_status", "alert_rule_id", "status"),
        Index("ix_alert_instances_security_id", "security_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_rule_id: Mapped[int] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AlertInstanceStatus.RESET, server_default="RESET"
    )
    last_trigger_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    alert_rule: Mapped[AlertRule] = relationship(back_populates="instances")
    security: Mapped[Security] = relationship(back_populates="alert_instances")


class Notification(Base):
    """A user-scoped notification attempt and its safe delivery outcome."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('FEISHU', 'WECHAT', 'EMAIL')",
            name="ck_notifications_channel",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED')",
            name="ck_notifications_status",
        ),
        Index("ix_notifications_user_created_at", "user_id", "created_at"),
        Index("ix_notifications_user_status", "user_id", "status"),
        Index("ix_notifications_alert_rule_id", "alert_rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    alert_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    security_id: Mapped[int | None] = mapped_column(
        ForeignKey("securities.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NotificationChannel.FEISHU
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NotificationStatus.PENDING, server_default="PENDING"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user: Mapped[User] = relationship(back_populates="notifications")
    alert_rule: Mapped[AlertRule | None] = relationship(back_populates="notifications")
    security: Mapped[Security | None] = relationship(back_populates="notifications")


__all__ = [
    "DailyBar",
    "QuoteSnapshot",
    "IndicatorSnapshot",
    "IndicatorState",
    "Invitation",
    "InvitationStatus",
    "AlertConditionType",
    "AlertInstance",
    "AlertInstanceStatus",
    "AlertRule",
    "RefreshSession",
    "Security",
    "StateDefinition",
    "User",
    "UserRole",
    "UserStatus",
    "WatchTable",
    "WatchTableSymbol",
    "TableColumn",
    "UserSetting",
    "Notification",
    "NotificationChannel",
    "NotificationStatus",
]
