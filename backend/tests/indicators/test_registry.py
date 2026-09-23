import pytest

from app.indicators import (
    IndicatorRegistry,
    InvalidParameterError,
    SMAResult,
    UnknownIndicatorError,
    create_default_registry,
)


class LastClosePlugin:
    name = "last_close"

    def calculate(self, series, **_: object) -> SMAResult:
        return SMAResult(value=float(tuple(series)[-1]), period=1)


def test_default_registry_dispatches_built_ins_by_name_and_parameters() -> None:
    registry = create_default_registry()

    result = registry.calculate("sma", [1.0, 2.0, 4.0], period=2)

    assert result.indicator == "ma"
    assert result.value == pytest.approx(3.0)


def test_registry_accepts_a_plugin_without_core_dispatch_changes() -> None:
    registry = IndicatorRegistry()
    registry.register(LastClosePlugin())

    result = registry.calculate("last_close", [1.0, 2.0, 4.0])

    assert result.value == pytest.approx(4.0)
    assert registry.names() == ("last_close",)


def test_registry_rejects_unknown_names() -> None:
    with pytest.raises(UnknownIndicatorError):
        IndicatorRegistry().calculate("missing", [1.0])


def test_registry_built_in_parameter_validation_is_not_silent() -> None:
    with pytest.raises(InvalidParameterError):
        create_default_registry().calculate("ma", [1.0, 2.0], period=0)
