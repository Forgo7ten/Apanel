"""Immutable input and output contracts for state recognition.

The state engine consumes a deliberately small snapshot shape instead of
depending on SQLAlchemy models or API schemas.  Mapping inputs are copied at
the boundary, so evaluating a state can never mutate a caller-owned payload.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from numbers import Real
from types import MappingProxyType
from typing import Any

from .errors import InvalidSnapshotError


class StateStatus(StrEnum):
    """The status shared by the table UI and the alert evaluator."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


def _finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise InvalidSnapshotError(f"{field_name} must be a real number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidSnapshotError(f"{field_name} must be a real number") from exc
    if not math.isfinite(result):
        raise InvalidSnapshotError(f"{field_name} must be finite")
    return result


def _freeze(value: Any) -> Any:
    """Make nested metadata immutable without retaining mutable caller state."""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return JSON-friendly copies of frozen nested metadata."""

    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any], *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return _freeze(value)


def _normalize_indicator(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSnapshotError("indicator must be a non-empty string")
    normalized = value.strip().upper()
    aliases = {"BOLLINGER": "BOLL", "SMA": "MA"}
    return aliases.get(normalized, normalized)


def _normalize_values(value: Mapping[str, Any], *, field_name: str) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise InvalidSnapshotError(f"{field_name} must be a mapping")
    normalized: dict[str, float] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise InvalidSnapshotError(f"{field_name} keys must be non-empty strings")
        normalized[raw_key.strip().lower()] = _finite_float(
            raw_value, field_name=f"{field_name}.{raw_key}"
        )
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True, init=False)
class IndicatorSnapshot:
    """A normalized, immutable latest indicator observation.

    ``previous_values`` is optional.  When present it lets a consumer rebuild
    the prior day's state without introducing a persistence dependency.  The
    engine still accepts a separate previous snapshot, which is the normal
    daily calculation seam.
    """

    indicator: str
    values: Mapping[str, float]
    previous_values: Mapping[str, float] | None
    parameters: Mapping[str, int | float]
    metadata: Mapping[str, Any]

    def __init__(
        self,
        indicator: str | None = None,
        values: Mapping[str, Any] | None = None,
        *,
        indicator_type: str | None = None,
        previous_values: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if indicator is not None and indicator_type is not None:
            if _normalize_indicator(indicator) != _normalize_indicator(indicator_type):
                raise InvalidSnapshotError("indicator and indicator_type do not match")
        resolved_indicator = indicator if indicator is not None else indicator_type
        if values is None:
            raise InvalidSnapshotError("values are required")
        normalized_values = _normalize_values(values, field_name="values")
        normalized_previous = (
            None
            if previous_values is None
            else _normalize_values(previous_values, field_name="previous_values")
        )
        raw_parameters = {} if parameters is None else parameters
        normalized_parameters: dict[str, int | float] = {}
        if not isinstance(raw_parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        for key, value in raw_parameters.items():
            if not isinstance(key, str) or not key.strip():
                raise InvalidSnapshotError("parameter keys must be non-empty strings")
            normalized_parameters[key.strip()] = _finite_float(
                value, field_name=f"parameters.{key}"
            )
        raw_metadata = {} if metadata is None else metadata
        normalized_metadata = _freeze_mapping(raw_metadata, field_name="metadata")
        object.__setattr__(self, "indicator", _normalize_indicator(resolved_indicator))
        object.__setattr__(self, "values", normalized_values)
        object.__setattr__(self, "previous_values", normalized_previous)
        object.__setattr__(self, "parameters", MappingProxyType(normalized_parameters))
        object.__setattr__(self, "metadata", normalized_metadata)

    @property
    def indicator_type(self) -> str:
        """Alias matching the database/API vocabulary."""

        return self.indicator

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> IndicatorSnapshot:
        if not isinstance(value, Mapping):
            raise InvalidSnapshotError("snapshot must be a mapping")
        indicator = value.get("indicator", value.get("indicator_type", value.get("type")))
        values = value.get("values")
        if values is None:
            # A small convenience for provider payloads that flatten scalar
            # indicator fields.  Reserved envelope keys never become values.
            reserved = {
                "indicator",
                "indicator_type",
                "type",
                "previous_values",
                "parameters",
                "metadata",
            }
            values = {key: item for key, item in value.items() if key not in reserved}
        return cls(
            indicator=indicator,
            values=values,
            previous_values=value.get("previous_values"),
            parameters=value.get("parameters"),
            metadata=value.get("metadata"),
        )

    @classmethod
    def from_result(cls, result: Any) -> IndicatorSnapshot:
        indicator = getattr(result, "indicator", None)
        values = getattr(result, "values", None)
        if indicator is None or values is None:
            raise InvalidSnapshotError("result must expose indicator and values")
        return cls(
            indicator=indicator,
            values=values,
            parameters=getattr(result, "parameters", None),
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "indicator": self.indicator,
            "values": dict(self.values),
            "parameters": dict(self.parameters),
            "metadata": _thaw(self.metadata),
        }
        if self.previous_values is not None:
            result["previous_values"] = dict(self.previous_values)
        return result


@dataclass(frozen=True, slots=True, init=False)
class PriceSnapshot:
    """Current and previous close prices used by BOLL break states."""

    current: float
    previous: float | None

    def __init__(self, current: Any, previous: Any | None = None) -> None:
        object.__setattr__(self, "current", _finite_float(current, field_name="price.current"))
        object.__setattr__(
            self,
            "previous",
            None if previous is None else _finite_float(previous, field_name="price.previous"),
        )


@dataclass(frozen=True, slots=True)
class StateResult:
    """One state observation shared by UI and Alert layers."""

    definition: Any
    active: bool
    transition: bool
    reason: str | None = None
    values: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise TypeError("state result values must be a mapping")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @property
    def code(self) -> str:
        return self.definition.code

    @property
    def state_code(self) -> str:
        return self.code

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def level(self) -> str:
        return self.definition.level

    @property
    def indicator_type(self) -> str:
        return self.definition.indicator_type

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self.definition.metadata

    @property
    def status(self) -> StateStatus:
        return StateStatus.ACTIVE if self.active else StateStatus.INACTIVE

    @property
    def entered(self) -> bool:
        """Alias used by edge-triggered alert consumers."""

        return self.transition

    @property
    def is_active(self) -> bool:
        return self.active

    def to_dict(self) -> dict[str, object]:
        return {
            "state_code": self.code,
            "name": self.name,
            "level": self.level,
            "indicator_type": self.indicator_type,
            "status": self.status.value,
            "active": self.active,
            "transition": self.transition,
            "metadata": _thaw(self.metadata),
            "values": dict(self.values),
            "reason": self.reason,
        }


# Public aliases keep the seam readable to callers that use ``Evaluation``
# terminology rather than the persisted ``StateResult`` name.
StateEvaluation = StateResult
StateObservation = StateResult
StateSnapshot = IndicatorSnapshot
