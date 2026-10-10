"""A Watch cell's exact field/period/adjustment survives the Alert API boundary."""

import pytest

from app.core.errors import ApiError
from app.indicators.parameters import parameter_key
from app.schemas.alerts import AlertRuleCreateRequest
from app.services.alert_service import _condition_fields


def normalize(payload: AlertRuleCreateRequest):
    return _condition_fields(
        condition_type=payload.condition_type,
        state_id=payload.state_id,
        state_code=payload.state_code,
        indicator=payload.indicator,
        operator=payload.operator,
        threshold=payload.threshold,
        parameters=payload.parameters,
        field=payload.field,
        adjust_type=payload.adjust_type,
    )


@pytest.mark.parametrize("adjustment", ["qfq", "none"])
def test_custom_watch_rsi_value_preserves_adjustment_and_period(adjustment):
    rule = AlertRuleCreateRequest(
        security_id=1, condition_type="VALUE", indicator="RSI",
        parameters={"period": 6}, field="value",
        adjust_type=adjustment, operator=">=", threshold=70,
    )
    target = normalize(rule)
    assert target["adjust_type"] == adjustment
    assert target["parameters"] == {"period": 6}
    assert target["parameter_key"] == parameter_key("RSI", {"period": 6})
    assert target["field"] == "value"


@pytest.mark.parametrize("field", ["MA20", "MA60"])
def test_multi_ma_watch_alert_exact_variant(field):
    rule = AlertRuleCreateRequest(
        security_id=1, condition_type="VALUE", indicator="MA",
        parameters={"periods": [20, 60]}, field=field,
        adjust_type="none", operator=">", threshold=12,
    )
    target = normalize(rule)
    assert target["field"] == field
    assert target["parameters"] == {"periods": [20, 60]}
    assert target["parameter_key"] == parameter_key("MA", {"periods": [20, 60]})
    assert target["adjust_type"] == "none"


def test_mismatched_ma_period_and_field_rejected():
    rule = AlertRuleCreateRequest(
        security_id=1, condition_type="VALUE", indicator="MA",
        parameters={"period": 5}, field="MA20",
        adjust_type="none", operator=">", threshold=12,
    )
    with pytest.raises(ApiError, match="MA alert requires"):
        normalize(rule)


def test_state_from_detail_stays_precisely_scoped():
    target = normalize(AlertRuleCreateRequest(
        security_id=1, condition_type="STATE", state_id="MA_CROSS_UP",
        parameters={"short_period": 20, "long_period": 60}, adjust_type="none",
    ))
    assert target["parameters"] == {"short_period": 20, "long_period": 60}
    assert target["adjust_type"] == "none"
    assert target["parameter_key"]
