from decimal import Decimal

import pytest

from app.indicators import (
    Candle,
    InsufficientDataError,
    InvalidInputError,
    calculate_projected_ma,
    calculate_sma,
)


def test_candle_normalizes_decimal_to_finite_float_without_mutable_state() -> None:
    candle = Candle(close=Decimal("12.30"))

    assert candle.close == pytest.approx(12.3)
    assert isinstance(candle.close, float)
    with pytest.raises((AttributeError, TypeError)):
        candle.close = 13.0  # type: ignore[misc]


@pytest.mark.parametrize("close", [0, -1, float("nan"), float("inf")])
def test_candle_rejects_non_positive_or_non_finite_prices(close: float) -> None:
    with pytest.raises(InvalidInputError):
        Candle(close=close)


def test_sma_returns_latest_window_and_keeps_input_unchanged() -> None:
    closes = [1.0, 2.0, 3.0, 4.0]
    before = closes.copy()

    result = calculate_sma(closes, period=3)

    assert result.value == pytest.approx(3.0)
    assert result.period == 3
    assert result.values == {"value": 3.0}
    assert result.to_snapshot() == {
        "indicator": "ma",
        "parameters": {"period": 3},
        "values": {"value": 3.0},
    }
    assert closes == before


def test_projected_ma_replaces_the_next_day_slot_with_the_latest_close() -> None:
    result = calculate_projected_ma([1.0, 2.0, 4.0, 5.0], period=3)

    assert result.value == pytest.approx((4.0 + 5.0 + 5.0) / 3.0)
    assert result.projected_close == pytest.approx(5.0)


def test_ma_rejects_a_short_series_with_a_domain_error() -> None:
    with pytest.raises(InsufficientDataError) as exc_info:
        calculate_sma([1.0, 2.0], period=3)

    assert exc_info.value.required == 3
    assert exc_info.value.available == 2


def test_projected_ma_requires_the_same_completed_window_as_sma() -> None:
    with pytest.raises(InsufficientDataError):
        calculate_projected_ma([1.0, 2.0], period=3)
