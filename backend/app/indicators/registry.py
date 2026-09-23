"""Name-based indicator registry with a stable plugin seam."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from .errors import ResultValidationError, UnknownIndicatorError
from .ma import ProjectedMAIndicator, SMAIndicator
from .results import IndicatorResult


class IndicatorRegistry:
    """Resolve and execute indicators without a hard-coded dispatch switch.

    A plugin only needs a ``name`` and ``calculate(series, **parameters)``.  A
    registered class is instantiated with no arguments; stateful plugins can
    instead register an already-created instance.
    """

    def __init__(self) -> None:
        self._indicators: dict[str, Any] = {}
        self._canonical_names: list[str] = []

    def register(
        self,
        indicator: Any,
        *,
        name: str | None = None,
        aliases: Iterable[str] = (),
        replace: bool = False,
    ) -> Any:
        instance = indicator() if inspect.isclass(indicator) else indicator
        resolved_name = name or getattr(instance, "name", None)
        if not isinstance(resolved_name, str) or not resolved_name.strip():
            raise ValueError("an indicator must expose a non-empty name")
        resolved_name = resolved_name.strip().lower()
        calculate = getattr(instance, "calculate", None)
        if not callable(calculate):
            raise ValueError(f"indicator {resolved_name!r} must expose calculate()")

        names = (resolved_name, *(str(alias).strip().lower() for alias in aliases))
        if any(not alias for alias in names):
            raise ValueError("indicator names and aliases must be non-empty")
        collisions = [alias for alias in names if alias in self._indicators and not replace]
        if collisions:
            raise ValueError(f"indicator name already registered: {collisions[0]}")
        for alias in names:
            self._indicators[alias] = instance
        if resolved_name not in self._canonical_names:
            self._canonical_names.append(resolved_name)
        return instance

    def get(self, name: str) -> Any:
        if not isinstance(name, str) or not name.strip():
            raise UnknownIndicatorError("indicator name must be non-empty")
        key = name.strip().lower()
        try:
            return self._indicators[key]
        except KeyError as exc:
            raise UnknownIndicatorError(f"unknown indicator: {name}") from exc

    def calculate(self, name: str, series: Any, **parameters: Any) -> IndicatorResult:
        result = self.get(name).calculate(series, **parameters)
        if not isinstance(result, IndicatorResult):
            raise ResultValidationError(
                f"indicator {name!r} returned {type(result).__name__}, expected IndicatorResult"
            )
        # Re-run the shared contract for third-party results whose constructor
        # may not have called the base validation hook.
        result.__post_init__()
        return result

    def names(self) -> tuple[str, ...]:
        return tuple(self._canonical_names)


def create_default_registry() -> IndicatorRegistry:
    """Create a fresh registry containing all v1 built-in indicators."""

    registry = IndicatorRegistry()
    registry.register(SMAIndicator(), aliases=("sma",))
    registry.register(ProjectedMAIndicator(), aliases=("pma",))

    # Imports stay local so registering a custom indicator never requires a
    # change to this dispatch path; built-ins simply opt into the same seam.
    from .boll import BollingerIndicator
    from .kdj import KDJIndicator
    from .macd import MACDIndicator
    from .rsi import RSIIndicator

    registry.register(RSIIndicator())
    registry.register(KDJIndicator())
    registry.register(BollingerIndicator(), aliases=("bollinger",))
    registry.register(MACDIndicator())
    return registry


DEFAULT_REGISTRY = create_default_registry()
registry = DEFAULT_REGISTRY
