import pytest

from app.indicators import (
    InsufficientDataError,
    InvalidParameterError,
    calculate_bollinger,
)


def test_bollinger_uses_population_standard_deviation_and_normalized_width() -> None:
    result = calculate_bollinger([1.0, 2.0, 3.0, 4.0], period=4, multiplier=2.0)

    mean = 2.5
    population_std = (1.25) ** 0.5
    upper = mean + (2.0 * population_std)
    lower = mean - (2.0 * population_std)
    assert result.middle == pytest.approx(mean)
    assert result.upper == pytest.approx(upper)
    assert result.lower == pytest.approx(lower)
    assert result.width == pytest.approx((upper - lower) / mean)


def test_bollinger_constant_series_has_zero_width() -> None:
    result = calculate_bollinger([7.0] * 20)

    assert result.upper == result.middle == result.lower == 7.0
    assert result.width == 0.0


def test_bollinger_rejects_short_series_and_non_positive_multiplier() -> None:
    with pytest.raises(InsufficientDataError):
        calculate_bollinger([1.0, 2.0], period=3)
    with pytest.raises(InvalidParameterError):
        calculate_bollinger([1.0, 2.0, 3.0], period=3, multiplier=0)
