from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.states import (
    IndicatorSnapshot,
    StateEngine,
    StateStatus,
    create_default_registry,
)


def snapshot(indicator: str, values: dict[str, float], **extra: object) -> IndicatorSnapshot:
    return IndicatorSnapshot(indicator=indicator, values=values, **extra)


def test_default_registry_exposes_fixed_state_definitions() -> None:
    registry = create_default_registry()

    assert registry.codes() == (
        "MA_CROSS_UP",
        "MA_CROSS_DOWN",
        "MA_GAP_NARROWING",
        "MA_GAP_EXPANDING",
        "KDJ_K_CROSS_D_UP",
        "KDJ_K_CROSS_D_DOWN",
        "KDJ_ALL_RISING",
        "KDJ_ALL_FALLING",
        "BOLL_WIDTH_NARROWING",
        "BOLL_WIDTH_EXPANDING",
        "BOLL_ALL_RISING",
        "BOLL_ALL_FALLING",
        "BOLL_BREAK_UPPER",
        "BOLL_BREAK_LOWER",
        "MACD_DIFF_CROSS_DEA_UP",
        "MACD_DIFF_CROSS_DEA_DOWN",
        "MACD_RED_BAR_GROWING",
        "MACD_RED_BAR_SHRINKING",
    )

    definition = registry.get("BOLL_WIDTH_NARROWING")
    assert definition.name == "带口收窄"
    assert definition.level == "WARNING"
    assert definition.indicator_type == "BOLL"
    assert definition.metadata["mode"] == "continuous"
    with pytest.raises(TypeError):
        definition.metadata["mode"] = "edge"  # type: ignore[index]


def test_evaluation_has_shared_ui_alert_fields_and_is_immutable() -> None:
    engine = StateEngine()
    current = snapshot("MA", {"short": 11.0, "long": 10.0})
    previous = snapshot("MA", {"short": 9.0, "long": 10.0})

    result = engine.evaluate_state("MA_CROSS_UP", current, previous)

    assert result.code == "MA_CROSS_UP"
    assert result.state_code == result.code
    assert result.name == "MA上穿"
    assert result.level == "POSITIVE"
    assert result.indicator_type == "MA"
    assert result.status == StateStatus.ACTIVE
    assert result.active is True
    assert result.transition is True
    assert result.entered is True
    with pytest.raises(FrozenInstanceError):
        result.active = False  # type: ignore[misc]


def test_snapshot_copies_input_and_rejects_invalid_metadata() -> None:
    values = {"short": 11.0, "long": 10.0}
    snap = snapshot("MA", values, metadata={"periods": [5, 10]})
    values["short"] = 999.0

    assert snap.values["short"] == 11.0
    with pytest.raises(TypeError):
        IndicatorSnapshot("MA", {"short": 1.0}, metadata=["not", "a", "mapping"])  # type: ignore[arg-type]
