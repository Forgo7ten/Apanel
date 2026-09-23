"""SQLAlchemy declarative base.

Sprint 0 intentionally defines no business models or tables.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for future SQLAlchemy models."""
