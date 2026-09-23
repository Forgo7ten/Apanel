from __future__ import annotations

from copy import deepcopy

import pytest

from app.states import IndicatorSnapshot, PriceSnapshot, StateEngine, StateStatus


def snap(indicator: str, values: dict[str, float], **kwargs: object) -> IndicatorSnapshot:
    return IndicatorSnapshot(indicator=indicator, values=values, **kwargs)


def evaluate(
    code: str,
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot | None = None,
    **kwargs: object,
):
    return StateEngine().evaluate_state(code, current, previous, **kwargs)


@pytest.mark.parametrize(
    ("code", "current", "previous"),
    [
        (
            "MA_CROSS_UP",
            snap("MA", {"short": 10.0, "long": 10.0 + 1e-13}),
            snap("MA", {"short": 9.0, "long": 10.0}),
        ),
        (
            "MA_CROSS_DOWN",
            snap("MA", {"short": 10.0, "long": 10.0 - 1e-13}),
            snap("MA", {"short": 11.0, "long": 10.0}),
        ),
    ],
)
def test_crossing_inside_float_tolerance_is_inactive(code: str, current, previous) -> None:
    result = evaluate(code, current, previous)

    assert result.status == StateStatus.INACTIVE
    assert result.transition is False


def test_crossing_through_exact_equality_is_an_edge() -> None:
    previous = snap("MA", {"short": 10.0, "long": 10.0})
    current = snap("MA", {"short": 10.0 + 1e-4, "long": 10.0})

    result = evaluate("MA_CROSS_UP", current, previous, tolerance=1e-6)

    assert result.active is True
    assert result.transition is True


@pytest.mark.parametrize("code", ["MA_CROSS_UP", "MA_CROSS_DOWN"])
def test_crossing_without_previous_is_inactive(code: str) -> None:
    result = evaluate(code, snap("MA", {"short": 11.0, "long": 10.0}))

    assert result.active is False
    assert result.transition is False
    assert result.reason == "missing_previous"


@pytest.mark.parametrize(
    ("code", "current", "previous"),
    [
        (
            "MA_GAP_NARROWING",
            snap("MA", {"short": 10.5, "long": 10.0}),
            snap("MA", {"short": 12.0, "long": 10.0}),
        ),
        (
            "MA_GAP_EXPANDING",
            snap("MA", {"short": 12.0, "long": 10.0}),
            snap("MA", {"short": 10.5, "long": 10.0}),
        ),
        (
            "KDJ_ALL_RISING",
            snap("KDJ", {"k": 61.0, "d": 55.0, "j": 73.0}),
            snap("KDJ", {"k": 60.0, "d": 54.0, "j": 72.0}),
        ),
        (
            "KDJ_ALL_FALLING",
            snap("KDJ", {"k": 59.0, "d": 53.0, "j": 71.0}),
            snap("KDJ", {"k": 60.0, "d": 54.0, "j": 72.0}),
        ),
        (
            "BOLL_WIDTH_NARROWING",
            snap("BOLL", {"upper": 11.0, "middle": 10.0, "lower": 9.2, "width": 0.18}),
            snap("BOLL", {"upper": 11.5, "middle": 10.0, "lower": 8.5, "width": 0.30}),
        ),
        (
            "BOLL_WIDTH_EXPANDING",
            snap("BOLL", {"upper": 12.0, "middle": 10.0, "lower": 8.0, "width": 0.40}),
            snap("BOLL", {"upper": 11.5, "middle": 10.0, "lower": 8.5, "width": 0.30}),
        ),
        (
            "BOLL_ALL_RISING",
            snap("BOLL", {"upper": 12.0, "middle": 10.5, "lower": 9.0, "width": 0.30}),
            snap("BOLL", {"upper": 11.5, "middle": 10.0, "lower": 8.5, "width": 0.30}),
        ),
        (
            "BOLL_ALL_FALLING",
            snap("BOLL", {"upper": 11.0, "middle": 9.5, "lower": 8.0, "width": 0.30}),
            snap("BOLL", {"upper": 11.5, "middle": 10.0, "lower": 8.5, "width": 0.30}),
        ),
        (
            "MACD_RED_BAR_GROWING",
            snap("MACD", {"diff": 1.2, "dea": 0.8, "histogram": 0.4}),
            snap("MACD", {"diff": 1.1, "dea": 0.8, "histogram": 0.3}),
        ),
        (
            "MACD_RED_BAR_SHRINKING",
            snap("MACD", {"diff": 1.0, "dea": 0.8, "histogram": 0.2}),
            snap("MACD", {"diff": 1.1, "dea": 0.8, "histogram": 0.3}),
        ),
    ],
)
def test_continuous_states_are_active_on_the_expected_direction(code, current, previous) -> None:
    result = evaluate(code, current, previous)

    assert result.active is True
    assert result.transition is True


def test_continuous_state_does_not_repeat_transition_while_active() -> None:
    engine = StateEngine()
    day1 = snap("KDJ", {"k": 50.0, "d": 50.0, "j": 50.0})
    day2 = snap("KDJ", {"k": 51.0, "d": 51.0, "j": 51.0})
    day3 = snap("KDJ", {"k": 52.0, "d": 52.0, "j": 52.0})

    first = engine.evaluate_state("KDJ_ALL_RISING", day2, day1)
    second = engine.evaluate_state("KDJ_ALL_RISING", day3, day2)

    assert first.active and first.transition
    assert second.active and not second.transition


def test_active_state_reenters_after_reverse_direction() -> None:
    engine = StateEngine()
    day1 = snap("BOLL", {"upper": 11.0, "middle": 10.0, "lower": 9.0, "width": 0.2})
    day2 = snap("BOLL", {"upper": 11.5, "middle": 10.5, "lower": 9.5, "width": 0.2})
    day3 = snap("BOLL", {"upper": 11.0, "middle": 10.0, "lower": 9.0, "width": 0.2})
    day4 = snap("BOLL", {"upper": 11.5, "middle": 10.5, "lower": 9.5, "width": 0.2})

    assert engine.evaluate_state("BOLL_ALL_RISING", day2, day1).transition
    assert not engine.evaluate_state("BOLL_ALL_RISING", day3, day2).active
    assert engine.evaluate_state("BOLL_ALL_RISING", day4, day3).transition


def test_kdj_cross_and_macd_cross_are_symmetric() -> None:
    kdj_up = evaluate(
        "KDJ_K_CROSS_D_UP",
        snap("KDJ", {"k": 51.0, "d": 50.0, "j": 52.0}),
        snap("KDJ", {"k": 49.0, "d": 50.0, "j": 48.0}),
    )
    macd_down = evaluate(
        "MACD_DIFF_CROSS_DEA_DOWN",
        snap("MACD", {"diff": 0.9, "dea": 1.0, "histogram": -0.1}),
        snap("MACD", {"diff": 1.1, "dea": 1.0, "histogram": 0.1}),
    )

    assert kdj_up.active and kdj_up.transition
    assert macd_down.active and macd_down.transition


def test_boll_break_requires_price_crossing_band_and_not_just_being_above() -> None:
    previous = snap("BOLL", {"upper": 100.0, "middle": 95.0, "lower": 90.0, "width": 0.1})
    current = snap("BOLL", {"upper": 101.0, "middle": 96.0, "lower": 91.0, "width": 0.1})

    result = evaluate(
        "BOLL_BREAK_UPPER",
        current,
        previous,
        price=PriceSnapshot(current=102.0, previous=99.0),
    )
    repeat = evaluate(
        "BOLL_BREAK_UPPER",
        current,
        previous,
        price=PriceSnapshot(current=102.0, previous=101.5),
    )

    assert result.active and result.transition
    assert repeat.active and not repeat.transition


def test_invalid_values_and_indicator_metadata_are_inactive_not_silent() -> None:
    invalid = {
        "indicator": "BOLL",
        "values": {"upper": 11.0, "middle": 10.0, "lower": 9.0, "width": "bad"},
    }
    result = evaluate("BOLL_WIDTH_NARROWING", invalid, invalid)

    assert result.active is False
    assert result.transition is False
    assert result.reason == "invalid_values"


def test_evaluation_does_not_modify_nested_input_mappings() -> None:
    current_values = {"k": 61.0, "d": 55.0, "j": 73.0}
    previous_values = {"k": 60.0, "d": 54.0, "j": 72.0}
    current_metadata = {"source": {"days": 1}}
    current = {"indicator": "KDJ", "values": current_values, "metadata": current_metadata}
    previous = {"indicator": "KDJ", "values": previous_values}
    before = deepcopy((current, previous))

    result = StateEngine().evaluate_state("KDJ_ALL_RISING", current, previous)

    assert result.active
    assert (current, previous) == before


def test_short_or_mismatched_snapshots_are_inactive() -> None:
    missing = evaluate(
        "KDJ_ALL_RISING",
        snap("KDJ", {"k": 60.0, "d": 55.0}),
        snap("KDJ", {"k": 59.0, "d": 54.0}),
    )
    mismatched = evaluate(
        "MA_CROSS_UP",
        snap("MA", {"short": 11.0, "long": 10.0}),
        snap("MACD", {"diff": 1.0, "dea": 0.5, "histogram": 0.5}),
    )

    assert missing.reason == "missing_values"
    assert mismatched.reason == "indicator_mismatch"
    assert not missing.active and not mismatched.active
