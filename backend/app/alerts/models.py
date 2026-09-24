"""Database-independent input and output contracts for alert evaluation.

The alert package deliberately contains no SQLAlchemy imports.  ``AlertRule``
and ``AlertTriggerState`` are the seams that a repository can persist later;
the evaluator only receives them and returns a new state.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from numbers import Real
from types import MappingProxyType
from typing import Any

from .errors import InvalidAlertObservationError, InvalidAlertRuleError


class AlertConditionType(StrEnum):
    """Kinds of conditions supported by the v1 alert engine."""

    VALUE = "VALUE"
    STATE = "STATE"


class AlertOperator(StrEnum):
    """Comparison operators allowed by VALUE conditions."""

    GREATER_THAN_OR_EQUAL = ">="
    GREATER_THAN = ">"
    LESS_THAN_OR_EQUAL = "<="
    LESS_THAN = "<"
    EQUAL = "=="


# Short names are convenient for callers constructing rules from API payloads.
ValueOperator = AlertOperator
ConditionType = AlertConditionType


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise InvalidAlertRuleError(f"{field_name} must be a real number")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidAlertRuleError(f"{field_name} must be a real number") from exc
    if not math.isfinite(normalized):
        raise InvalidAlertRuleError(f"{field_name} must be finite")
    return normalized


def _non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidAlertRuleError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_operator(value: AlertOperator | str) -> AlertOperator:
    try:
        return value if isinstance(value, AlertOperator) else AlertOperator(str(value).strip())
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(operator.value for operator in AlertOperator)
        raise InvalidAlertRuleError(f"operator must be one of: {allowed}") from exc


@dataclass(frozen=True, slots=True)
class ValueCondition:
    """A numeric comparison such as ``RSI >= 70``."""

    indicator: str
    operator: AlertOperator
    threshold: float

    def __init__(
        self,
        indicator: str,
        operator: AlertOperator | str,
        threshold: Real | Decimal,
    ) -> None:
        object.__setattr__(self, "indicator", _non_empty_text(indicator, field_name="indicator"))
        object.__setattr__(self, "operator", _normalize_operator(operator))
        object.__setattr__(self, "threshold", _finite_number(threshold, field_name="threshold"))

    @property
    def condition_type(self) -> AlertConditionType:
        return AlertConditionType.VALUE

    def matches(self, value: Real | Decimal) -> bool:
        """Return whether a finite value satisfies this condition."""

        current = _finite_number(value, field_name="value")
        if self.operator is AlertOperator.GREATER_THAN_OR_EQUAL:
            return current >= self.threshold
        if self.operator is AlertOperator.GREATER_THAN:
            return current > self.threshold
        if self.operator is AlertOperator.LESS_THAN_OR_EQUAL:
            return current <= self.threshold
        if self.operator is AlertOperator.LESS_THAN:
            return current < self.threshold
        return current == self.threshold


@dataclass(frozen=True, slots=True, init=False)
class StateCondition:
    """A condition matching one registered indicator state ID."""

    state_id: str

    def __init__(self, state_id: str | None = None, *, state_code: str | None = None) -> None:
        if state_id is not None and state_code is not None:
            if state_id.strip().upper() != state_code.strip().upper():
                raise InvalidAlertRuleError("state_id and state_code do not match")
        resolved = state_id if state_id is not None else state_code
        object.__setattr__(self, "state_id", _non_empty_text(resolved, field_name="state_id"))

    @property
    def state_code(self) -> str:
        """Alias used by the state engine and persisted schema."""

        return self.state_id

    @property
    def condition_type(self) -> AlertConditionType:
        return AlertConditionType.STATE


type AlertCondition = ValueCondition | StateCondition


@dataclass(frozen=True, slots=True, init=False)
class AlertRule:
    """A normalized, persistence-independent alert rule.

    ``condition`` is the preferred constructor argument.  ``from_mapping``
    accepts the flat API shape from the product documents, making it possible
    to keep API validation separate from the evaluator without duplicating
    condition parsing.
    """

    condition: AlertCondition
    security_id: int | str | None
    enabled: bool
    rule_id: int | str | None

    def __init__(
        self,
        condition: AlertCondition | None = None,
        security_id: int | str | None = None,
        enabled: bool = True,
        rule_id: int | str | None = None,
        *,
        condition_type: AlertConditionType | str | None = None,
        indicator: str | None = None,
        operator: AlertOperator | str | None = None,
        threshold: Real | Decimal | None = None,
        state_id: str | None = None,
        state_code: str | None = None,
    ) -> None:
        if condition is not None and condition_type is not None:
            raise InvalidAlertRuleError("condition and condition_type cannot both be provided")
        if condition is None:
            if condition_type is None:
                raise InvalidAlertRuleError("condition is required")
            try:
                kind = (
                    condition_type
                    if isinstance(condition_type, AlertConditionType)
                    else AlertConditionType(str(condition_type).strip().upper())
                )
            except (TypeError, ValueError) as exc:
                raise InvalidAlertRuleError("condition_type must be VALUE or STATE") from exc
            if kind is AlertConditionType.VALUE:
                if indicator is None or operator is None or threshold is None:
                    raise InvalidAlertRuleError(
                        "VALUE rules require indicator, operator, and threshold"
                    )
                condition = ValueCondition(indicator, operator, threshold)
            else:
                condition = StateCondition(state_id, state_code=state_code)
        if not isinstance(condition, (ValueCondition, StateCondition)):
            raise InvalidAlertRuleError("condition must be ValueCondition or StateCondition")
        if not isinstance(enabled, bool):
            raise InvalidAlertRuleError("enabled must be a boolean")
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "security_id", security_id)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "rule_id", rule_id)

    @property
    def condition_type(self) -> AlertConditionType:
        return self.condition.condition_type

    @property
    def indicator(self) -> str | None:
        return self.condition.indicator if isinstance(self.condition, ValueCondition) else None

    @property
    def operator(self) -> AlertOperator | None:
        return self.condition.operator if isinstance(self.condition, ValueCondition) else None

    @property
    def threshold(self) -> float | None:
        return self.condition.threshold if isinstance(self.condition, ValueCondition) else None

    @property
    def state_id(self) -> str | None:
        return self.condition.state_id if isinstance(self.condition, StateCondition) else None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AlertRule:
        if not isinstance(value, Mapping):
            raise InvalidAlertRuleError("rule must be a mapping")
        return cls(
            security_id=value.get("security_id"),
            enabled=value.get("enabled", True),
            rule_id=value.get("id", value.get("rule_id")),
            condition_type=value.get("condition_type", value.get("type")),
            indicator=value.get("indicator"),
            operator=value.get("operator"),
            threshold=value.get("threshold"),
            state_id=value.get("state_id"),
            state_code=value.get("state_code"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "security_id": self.security_id,
            "condition_type": self.condition_type.value,
            "enabled": self.enabled,
        }
        if self.rule_id is not None:
            result["id"] = self.rule_id
        if isinstance(self.condition, ValueCondition):
            result.update(
                {
                    "indicator": self.condition.indicator,
                    "operator": self.condition.operator.value,
                    "threshold": self.condition.threshold,
                }
            )
        else:
            result["state_id"] = self.condition.state_id
        return result


class AlertStatus(StrEnum):
    """Durable status values for an alert rule/security pair."""

    ACTIVE = "ACTIVE"
    RESET = "RESET"


@dataclass(frozen=True, slots=True)
class AlertTriggerState:
    """The minimal state callers persist between evaluator invocations."""

    status: AlertStatus = AlertStatus.RESET

    def __post_init__(self) -> None:
        try:
            normalized = (
                self.status
                if isinstance(self.status, AlertStatus)
                else AlertStatus(self.status)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidAlertRuleError("status must be ACTIVE or RESET") from exc
        object.__setattr__(self, "status", normalized)

    @property
    def active(self) -> bool:
        return self.status is AlertStatus.ACTIVE

    @classmethod
    def from_value(
        cls, value: AlertTriggerState | AlertStatus | bool | str | None
    ) -> AlertTriggerState:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return cls(AlertStatus.ACTIVE if value else AlertStatus.RESET)
        if isinstance(value, AlertStatus):
            return cls(value)
        try:
            return cls(AlertStatus(str(value).upper()))
        except (TypeError, ValueError) as exc:
            raise InvalidAlertRuleError("previous state must be ACTIVE or RESET") from exc


@dataclass(frozen=True, slots=True)
class AlertObservation:
    """Optional normalized input for an alert evaluation.

    ``values`` is keyed by indicator (for example ``{"RSI": 71}``) and
    ``states`` maps fixed state IDs to their active bit.  The scalar aliases
    are useful when evaluating one rule at a time and keep callers from
    having to manufacture a mapping.
    """

    values: Mapping[str, float]
    states: frozenset[str]
    state_values: Mapping[str, bool]
    value: float | None
    state_value: bool | None

    def __init__(
        self,
        values: Mapping[str, Real | Decimal] | None = None,
        states: Collection[str] | Mapping[str, bool] | None = None,
        *,
        value: Real | Decimal | None = None,
        current_value: Real | Decimal | None = None,
        state_value: bool | None = None,
    ) -> None:
        if value is not None and current_value is not None:
            raise InvalidAlertObservationError("value and current_value cannot both be provided")
        if state_value is not None and not isinstance(state_value, bool):
            raise InvalidAlertObservationError("state_value must be a boolean")
        scalar = value if value is not None else current_value
        normalized_values: dict[str, float] = {}
        if values is not None:
            if not isinstance(values, Mapping):
                raise InvalidAlertObservationError("values must be a mapping")
            for key, raw in values.items():
                if not isinstance(key, str) or not key.strip():
                    raise InvalidAlertObservationError(
                        "value indicator keys must be non-empty strings"
                    )
                try:
                    normalized_values[key.strip()] = _finite_number(raw, field_name=f"values.{key}")
                except InvalidAlertRuleError as exc:
                    raise InvalidAlertObservationError(str(exc)) from exc
        normalized_states: dict[str, bool] = {}
        if states is not None:
            if isinstance(states, Mapping):
                for key, active in states.items():
                    if not isinstance(key, str) or not key.strip():
                        raise InvalidAlertObservationError("state keys must be non-empty strings")
                    if not isinstance(active, bool):
                        raise InvalidAlertObservationError("state values must be booleans")
                    normalized_states[key.strip()] = active
            else:
                try:
                    state_codes = tuple(states)
                except TypeError as exc:
                    raise InvalidAlertObservationError("states must be a collection") from exc
                for code in state_codes:
                    if not isinstance(code, str) or not code.strip():
                        raise InvalidAlertObservationError("state IDs must be non-empty strings")
                    normalized_states[code.strip()] = True
        normalized_scalar: float | None = None
        if scalar is not None:
            try:
                normalized_scalar = _finite_number(scalar, field_name="value")
            except InvalidAlertRuleError as exc:
                raise InvalidAlertObservationError(str(exc)) from exc
        object.__setattr__(self, "values", MappingProxyType(normalized_values))
        object.__setattr__(self, "state_values", MappingProxyType(normalized_states))
        object.__setattr__(
            self,
            "states",
            frozenset(key for key, active in normalized_states.items() if active),
        )
        object.__setattr__(self, "value", normalized_scalar)
        object.__setattr__(self, "state_value", state_value)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AlertObservation:
        if not isinstance(value, Mapping):
            raise InvalidAlertObservationError("observation must be a mapping")
        values = value.get("values")
        if values is None:
            values = value.get("indicator_values")
        states = value.get("states", value.get("active_states"))
        scalar = value.get("value", value.get("current_value"))
        state_value = value.get("state_value")
        if state_value is None and "state" in value and isinstance(value.get("state"), bool):
            state_value = value["state"]
        if states is None:
            state_code = value.get("state_id", value.get("state_code"))
            active = value.get("active")
            if isinstance(state_code, str) and isinstance(active, bool):
                states = {state_code: active}
            elif isinstance(value.get("state"), str):
                states = [value["state"]]
        if values is None and scalar is None:
            # Flat indicator payloads are convenient for scheduler output.
            reserved = {
                "states",
                "active_states",
                "value",
                "current_value",
                "previous_value",
                "state",
                "state_id",
                "state_code",
                "active",
            }
            flattened = {
                str(key): raw
                for key, raw in value.items()
                if key not in reserved and isinstance(key, str)
            }
            values = flattened or None
        return cls(values=values, states=states, value=scalar, state_value=state_value)


@dataclass(frozen=True, slots=True)
class AlertEvaluation:
    """Result of one stateless edge-trigger evaluation."""

    satisfied: bool
    triggered: bool
    state: AlertTriggerState
    reason: str | None = None

    @property
    def active(self) -> bool:
        return self.satisfied

    @property
    def should_notify(self) -> bool:
        return self.triggered

    @property
    def next_state(self) -> AlertTriggerState:
        return self.state

    @property
    def status(self) -> AlertStatus:
        return self.state.status


__all__ = [
    "AlertCondition",
    "AlertConditionType",
    "AlertEvaluation",
    "AlertObservation",
    "AlertOperator",
    "AlertRule",
    "AlertStatus",
    "AlertTriggerState",
    "ConditionType",
    "StateCondition",
    "ValueCondition",
    "ValueOperator",
]
