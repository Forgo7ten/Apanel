from __future__ import annotations

from datetime import date

import pytest

from app.alerts import (
    AlertEvaluator,
    AlertObservation,
    AlertRule,
    AlertStatus,
    AlertTriggerState,
    StateCondition,
    ValueCondition,
    evaluate_alert,
)
from app.providers.notification import NotificationMessage


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        (">=", 70, True),
        (">=", 69.99, False),
        (">", 70, False),
        (">", 70.01, True),
        ("<=", 70, True),
        ("<", 70, False),
        ("<", 69.99, True),
        ("==", 70, True),
        ("==", 70.01, False),
    ],
)
def test_value_condition_operators_include_boundaries(operator, value, expected) -> None:
    rule = AlertRule(ValueCondition("RSI", operator, 70))

    result = evaluate_alert(rule, {"values": {"RSI": value}})

    assert result.satisfied is expected


def test_value_rule_accepts_flat_api_mapping_and_normalizes_operator() -> None:
    rule = AlertRule.from_mapping(
        {
            "security_id": 1,
            "condition_type": "VALUE",
            "indicator": "RSI",
            "operator": ">=",
            "threshold": 70,
        }
    )

    assert rule.to_dict() == {
        "security_id": 1,
        "condition_type": "VALUE",
        "enabled": True,
        "indicator": "RSI",
        "operator": ">=",
        "threshold": 70.0,
    }


def test_state_condition_accepts_active_state_mapping() -> None:
    rule = AlertRule(condition=StateCondition(state_code="BOLL_WIDTH_NARROWING"))

    assert evaluate_alert(rule, {"states": {"BOLL_WIDTH_NARROWING": True}}).satisfied
    assert not evaluate_alert(rule, {"states": {"BOLL_WIDTH_NARROWING": False}}).satisfied
    assert evaluate_alert(rule, {"states": ["boll_width_narrowing"]}).satisfied
    assert evaluate_alert(rule, {"state_id": "BOLL_WIDTH_NARROWING", "active": True}).satisfied


def test_edge_trigger_enters_once_resets_and_reenters() -> None:
    evaluator = AlertEvaluator()
    rule = AlertRule(ValueCondition("RSI", ">=", 70))
    reset = AlertTriggerState()

    first = evaluator.evaluate(rule, current_value=71, previous_state=reset)
    held = evaluator.evaluate(rule, current_value=72, previous_state=first.next_state)
    exited = evaluator.evaluate(rule, current_value=69, previous_state=held.next_state)
    reentered = evaluator.evaluate(rule, current_value=70, previous_state=exited.next_state)

    assert first.triggered and first.status is AlertStatus.ACTIVE
    assert held.satisfied and not held.triggered
    assert not exited.satisfied and exited.status is AlertStatus.RESET
    assert reentered.triggered


def test_edge_trigger_has_no_process_local_history() -> None:
    rule = AlertRule(ValueCondition("RSI", ">=", 70))
    first = AlertEvaluator().evaluate(rule, current_value=71)
    second = AlertEvaluator().evaluate(rule, current_value=71)

    assert first.triggered
    assert second.triggered


def test_disabled_rule_does_not_preserve_active_state() -> None:
    rule = AlertRule(ValueCondition("RSI", ">=", 70), enabled=False)

    result = evaluate_alert(rule, current_value=71, previous_state=AlertStatus.ACTIVE)

    assert not result.satisfied
    assert not result.triggered
    assert result.reason == "disabled"
    assert result.status is AlertStatus.RESET


def test_observation_and_notification_message_are_immutable_contracts() -> None:
    observation = AlertObservation(values={"RSI": 71}, states=["MA_CROSS_UP"])
    message = NotificationMessage(
        stock_name="贵州茅台",
        stock_code="600519",
        indicator="RSI",
        state="RSI_OVERBOUGHT",
        current_value=71,
        previous_value=69,
        date=date(2026, 9, 24),
    )

    assert observation.values["RSI"] == 71.0
    assert message.change == 2.0
    assert "当前值：71" in message.render_text()
    assert message.to_dict()["date"] == "2026-09-24"


def test_invalid_rule_and_non_finite_observation_are_rejected_or_reset() -> None:
    with pytest.raises(ValueError, match="operator"):
        ValueCondition("RSI", "!=", 70)
    with pytest.raises(ValueError, match="finite"):
        ValueCondition("RSI", ">=", float("inf"))

    rule = AlertRule(ValueCondition("RSI", ">=", 70))
    result = evaluate_alert(rule, current_value=float("nan"))

    assert not result.satisfied
    assert not result.triggered
    assert result.status is AlertStatus.RESET
