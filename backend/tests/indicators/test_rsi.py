import pytest

from app.indicators import InsufficientDataError, InvalidParameterError, calculate_rsi


def test_rsi_uses_wilder_smoothing_for_a_mixed_series() -> None:
    result = calculate_rsi([10.0, 12.0, 11.0, 13.0, 12.0, 14.0], period=3)

    # Initial averages: gain=4/3, loss=1/3.  Two Wilder updates later:
    # gain=1.259259..., loss=0.370370..., RSI=77.272727...
    assert result.value == pytest.approx(77.2727272727, rel=1e-10)
    assert result.average_gain == pytest.approx(1.2592592593, rel=1e-10)
    assert result.average_loss == pytest.approx(0.3703703704, rel=1e-10)


def test_rsi_with_no_down_moves_is_exactly_100() -> None:
    result = calculate_rsi(range(1, 16), period=14)

    assert result.value == 100.0
    assert result.average_loss == 0.0


def test_rsi_constant_series_is_neutral_and_not_nan() -> None:
    result = calculate_rsi([5.0] * 15, period=14)

    assert result.value == 50.0
    assert result.average_gain == 0.0
    assert result.average_loss == 0.0


def test_rsi_with_no_up_moves_is_zero() -> None:
    result = calculate_rsi(range(15, 0, -1), period=14)

    assert result.value == 0.0


def test_rsi_requires_period_plus_one_closes() -> None:
    with pytest.raises(InsufficientDataError) as exc_info:
        calculate_rsi([1.0, 2.0, 3.0], period=3)

    assert exc_info.value.required == 4
    assert exc_info.value.available == 3


def test_rsi_rejects_invalid_period() -> None:
    with pytest.raises(InvalidParameterError):
        calculate_rsi([1.0, 2.0], period=0)
