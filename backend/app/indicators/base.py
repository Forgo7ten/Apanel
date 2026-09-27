"""Structural plugin contract for indicator implementations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar, Protocol, runtime_checkable

from .results import IndicatorResult


@runtime_checkable
class SeriesIndicator(Protocol):
    """Optional optimized contract for one-pass historical calculation."""

    name: ClassVar[str]

    def calculate_series(
        self, series: Iterable[Any], **parameters: Any
    ) -> tuple[IndicatorResult | None, ...]: ...


class Indicator(Protocol):
    """Minimal contract required for registration of a new indicator."""

    name: ClassVar[str]

    def calculate(self, series: Iterable[Any], **parameters: Any) -> IndicatorResult:
        """Calculate a latest value from an immutable copy of the input series."""
