"""Pure edge-trigger evaluation for alert rules."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from decimal import Decimal
from numbers import Real
from typing import Any

from .errors import InvalidAlertObservationError
from .models import (
    AlertCondition,
    AlertEvaluation,
    AlertObservation,
    AlertRule,
    AlertStatus,
    AlertTriggerState,
    StateCondition,
    ValueCondition,
)


def _case_insensitive_lookup(values: Mapping[str, Any], key: str) -> Any | None:
    if key in values:
        return values[key]
    normalized = key.casefold()
    for candidate, value in values.items():
        if isinstance(candidate, str) and candidate.casefold() == normalized:
            return value
    return None


def _normalize_observation(
    observation: AlertObservation | Mapping[str, Any] | Real | Decimal | bool | None,
    *,
    current_value: Real | Decimal | None,
    active_states: Collection[str] | Mapping[str, bool] | None,
) -> AlertObservation:
    if isinstance(observation, AlertObservation):
        if current_value is not None or active_states is not None:
            raise InvalidAlertObservationError(
                "observation cannot be combined with current_value or active_states"
            )
        return observation
    if observation is None:
        return AlertObservation(value=current_value, states=active_states)
    if isinstance(observation, Mapping):
        normalized = AlertObservation.from_mapping(observation)
        if current_value is None and active_states is None:
            return normalized
        return AlertObservation(
            values=normalized.values,
            states=normalized.state_values if active_states is None else active_states,
            value=normalized.value if current_value is None else current_value,
        )
    if isinstance(observation, bool):
        if active_states is not None:
            raise InvalidAlertObservationError("observation cannot be combined with active_states")
        return AlertObservation(value=current_value, state_value=observation)
    if isinstance(observation, (Real, Decimal)):
        if current_value is not None:
            raise InvalidAlertObservationError("observation cannot be combined with current_value")
        return AlertObservation(value=observation, states=active_states)
    raise InvalidAlertObservationError("observation must be a mapping, number, boolean, or None")


def _state_satisfied(condition: StateCondition, observation: AlertObservation) -> bool:
    if observation.state_value is not None:
        return observation.state_value
    state_value = _case_insensitive_lookup(observation.state_values, condition.state_id)
    if state_value is not None:
        return bool(state_value)
    return any(code.casefold() == condition.state_id.casefold() for code in observation.states)


def _value_satisfied(condition: ValueCondition, observation: AlertObservation) -> bool:
    current = observation.value
    if current is None:
        candidate = _case_insensitive_lookup(observation.values, condition.indicator)
        if candidate is None:
            return False
        current = candidate
    return condition.matches(current)


def _condition_satisfied(condition: AlertCondition, observation: AlertObservation) -> bool:
    if isinstance(condition, ValueCondition):
        return _value_satisfied(condition, observation)
    return _state_satisfied(condition, observation)


class AlertEvaluator:
    """Stateless evaluator implementing the alert Edge Trigger contract.

    No rule history is stored on this object.  ``previous_state`` may be a
    persisted ``AlertTriggerState``, a boolean, or ``None`` for RESET.  The
    returned ``AlertEvaluation.state`` is the only state needed by the next
    call.
    """

    def evaluate(
        self,
        rule: AlertRule,
        observation: AlertObservation | Mapping[str, Any] | Real | Decimal | bool | None = None,
        previous_state: AlertTriggerState | AlertStatus | bool | str | None = None,
        *,
        current_value: Real | Decimal | None = None,
        active_states: Collection[str] | Mapping[str, bool] | None = None,
    ) -> AlertEvaluation:
        if not isinstance(rule, AlertRule):
            raise TypeError("rule must be an AlertRule")
        prior = AlertTriggerState.from_value(previous_state)
        if not rule.enabled:
            return AlertEvaluation(False, False, AlertTriggerState(), reason="disabled")
        try:
            normalized = _normalize_observation(
                observation,
                current_value=current_value,
                active_states=active_states,
            )
            satisfied = _condition_satisfied(rule.condition, normalized)
        except InvalidAlertObservationError as exc:
            return AlertEvaluation(False, False, AlertTriggerState(), reason=str(exc))
        next_state = AlertTriggerState(AlertStatus.ACTIVE if satisfied else AlertStatus.RESET)
        triggered = satisfied and not prior.active
        return AlertEvaluation(satisfied, triggered, next_state)


def evaluate_alert(
    rule: AlertRule,
    observation: AlertObservation | Mapping[str, Any] | Real | Decimal | bool | None = None,
    previous_state: AlertTriggerState | AlertStatus | bool | str | None = None,
    **kwargs: Any,
) -> AlertEvaluation:
    """Functional convenience wrapper around :class:`AlertEvaluator`."""

    return AlertEvaluator().evaluate(rule, observation, previous_state, **kwargs)


__all__ = ["AlertEvaluator", "evaluate_alert"]
