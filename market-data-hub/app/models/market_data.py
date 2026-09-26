"""SQLAlchemy models for shared market data persistence.

The domain records deliberately stay independent from SQLAlchemy.  These
models are the storage mapping only: repositories translate between them and
the validated domain DTOs.  PostgreSQL ``NUMERIC`` columns are left
unconstrained so a provider's :class:`~decimal.Decimal` value is never
silently rounded, and all instants use timezone-aware timestamps.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for market-data tables.

    Alembic owns schema creation in a later migration slice.  This module
    only exposes metadata for that migration and for dialect-level tests.
    """


class SecurityModel(Base):
    """Persistent security metadata keyed by its canonical six-digit symbol."""

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(6), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    exchange: Mapped[str] = mapped_column(String(2), nullable=False)
    security_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailyBarModel(Base):
    """Historical OHLCV data, unique per security/date/adjustment mode."""

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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True), nullable=True)
    adjust_type: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuoteSnapshotModel(Base):
    """Intraday quote snapshots, unique per security and UTC instant."""

    __tablename__ = "quote_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "timestamp",
            name="uq_quote_snapshots_security_timestamp",
        ),
        Index("ix_quote_snapshots_security_timestamp", "security_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    change: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True), nullable=True)
    change_percent: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DividendEventModel(Base):
    """Cash dividend event, unique for one security and event date."""

    __tablename__ = "dividend_events"
    __table_args__ = (
        UniqueConstraint("security_id", "date", name="uq_dividend_events_security_date"),
        Index("ix_dividend_events_security_date", "security_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    cash_amount: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Explicit aliases make the persistence boundary discoverable to callers
# without colliding with the public domain DTO names.
SecurityRecord = SecurityModel
DailyBarRecord = DailyBarModel
QuoteSnapshotRecord = QuoteSnapshotModel
DividendEventRecord = DividendEventModel
Security = SecurityModel
DailyBar = DailyBarModel
QuoteSnapshot = QuoteSnapshotModel
DividendEvent = DividendEventModel


__all__ = [
    "Base",
    "DailyBarModel",
    "DailyBarRecord",
    "DailyBar",
    "DividendEvent",
    "DividendEventModel",
    "DividendEventRecord",
    "QuoteSnapshot",
    "QuoteSnapshotModel",
    "QuoteSnapshotRecord",
    "Security",
    "SecurityModel",
    "SecurityRecord",
]
