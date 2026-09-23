"""Structural plugin contract for indicator implementations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar, Protocol, runtime_checkable

from .results import IndicatorResult


@runtime_checkable
class Indicator(Protocol):
    """Minimal contract required for registration of a new indicator."""

    name: ClassVar[str]

    def calculate(self, series: Iterable[Any], **parameters: Any) -> IndicatorResult:
        """Calculate a latest value from an immutable copy of the input series."""
