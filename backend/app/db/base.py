"""SQLAlchemy declarative base.

Sprint 0 intentionally defines no business models or tables.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for future SQLAlchemy models."""


# Keep metadata complete for callers (and migration tooling) that import only
# ``Base``. The models module imports ``Base`` above, so this late import avoids
# a partially initialized declarative base.
from app import models as _models  # noqa: E402,F401
