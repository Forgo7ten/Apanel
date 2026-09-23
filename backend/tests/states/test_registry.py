from __future__ import annotations

from app.states import (
    IndicatorSnapshot,
    StateDefinition,
    StateEngine,
    StateRegistry,
    StateResult,
)


def test_registry_accepts_plugin_without_core_dispatch_change() -> None:
    registry = StateRegistry()

    def evaluate(current, previous, *, price, previous_price, tolerance):
        return current.values.get("value") == 1.0

    registry.register(
        StateDefinition(
            code="CUSTOM_READY",
            name="自定义就绪",
            level="INFO",
            indicator_type="CUSTOM",
            metadata={"mode": "continuous", "fields": ("value",)},
            evaluator=evaluate,
        )
    )

    result = StateEngine(registry).evaluate_state(
        "CUSTOM_READY",
        IndicatorSnapshot("CUSTOM", {"value": 1.0}),
        IndicatorSnapshot("CUSTOM", {"value": 0.0}),
    )

    assert isinstance(result, StateResult)
    assert result.active
    assert registry.codes() == ("CUSTOM_READY",)
