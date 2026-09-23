from decimal import Decimal
from math import isfinite

import pytest

from app.indicators import (
    Candle,
    InvalidInputError,
    ResultValidationError,
    SMAResult,
    calculate_bollinger,
    calculate_kdj,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    normalized_band_width,
)


def decimal_ohlc(rows: list[tuple[str, str, str]]) -> list[Candle]:
    return [
        Candle(
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
        )
        for high, low, close in rows
    ]


def test_decimal_and_float_inputs_follow_the_same_float_policy() -> None:
    float_closes = [1.0, 2.0, 4.0, 3.0, 5.0]
    decimal_closes = [Decimal(str(value)) for value in float_closes]

    assert calculate_sma(decimal_closes, 3).value == pytest.approx(
        calculate_sma(float_closes, 3).value
    )
    assert calculate_rsi(decimal_closes, 3).value == pytest.approx(
        calculate_rsi(float_closes, 3).value
    )
    assert calculate_bollinger(decimal_closes, 3, Decimal("2")).width == pytest.approx(
        calculate_bollinger(float_closes, 3, 2.0).width
    )
    assert calculate_macd(decimal_closes, 2, 3, 2).histogram == pytest.approx(
        calculate_macd(float_closes, 2, 3, 2).histogram
    )


def test_decimal_and_float_ohlc_inputs_are_equivalent_for_kdj() -> None:
    rows = [("10", "8", "9"), ("12", "9", "11"), ("11", "9", "10")]
    decimal_result = calculate_kdj(decimal_ohlc(rows), period=2, k_period=2, d_period=2)
    float_result = calculate_kdj(
        [Candle(high=float(high), low=float(low), close=float(close)) for high, low, close in rows],
        period=2,
        k_period=2,
        d_period=2,
    )

    assert decimal_result.values == pytest.approx(float_result.values)


def test_results_reject_non_finite_values_instead_of_spreading_them() -> None:
    with pytest.raises(ResultValidationError):
        SMAResult(value=float("nan"), period=5)

    with pytest.raises(InvalidInputError):
        calculate_sma([1.0, float("inf")], period=2)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_every_supplied_price_field_must_be_positive(field: str) -> None:
    values = {"high": 2.0, "low": 1.0, "close": 1.5}
    values[field] = 0.0
    with pytest.raises(InvalidInputError):
        Candle(**values)


def test_zero_middle_width_is_defined_as_zero() -> None:
    assert normalized_band_width(upper=1.0, lower=-1.0, middle=0.0) == 0.0


def test_normal_results_are_finite() -> None:
    result = calculate_bollinger([1.0, 2.0, 3.0], period=3)
    assert all(isfinite(value) for value in result.values.values())
