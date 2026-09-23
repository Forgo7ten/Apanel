import pytest

from app.indicators import (
    InsufficientDataError,
    InvalidParameterError,
    calculate_macd,
)


def reference_ema(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    output.append(seed)
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for value in values[period:]:
        previous = (alpha * value) + ((1.0 - alpha) * previous)
        output.append(previous)
    return output


def reference_macd(
    values: list[float], fast: int, slow: int, signal: int
) -> tuple[float, float, float]:
    fast_ema = reference_ema(values, fast)
    slow_ema = reference_ema(values, slow)
    diffs = [
        fast_ema[index] - slow_ema[index]
        for index in range(slow - 1, len(values))
        if fast_ema[index] is not None and slow_ema[index] is not None
    ]
    signal_ema = reference_ema(diffs, signal)
    diff = diffs[-1]
    dea = signal_ema[-1]
    assert dea is not None
    return diff, dea, diff - dea


def test_macd_matches_independent_sma_seeded_ema_reference() -> None:
    closes = [1.0, 2.0, 4.0, 3.0, 5.0, 4.0, 6.0, 8.0, 7.0]
    expected = reference_macd(closes, fast=3, slow=5, signal=2)

    result = calculate_macd(closes, fast_period=3, slow_period=5, signal_period=2)

    assert result.diff == pytest.approx(expected[0])
    assert result.dea == pytest.approx(expected[1])
    assert result.histogram == pytest.approx(expected[2])
    assert result.histogram == pytest.approx(result.diff - result.dea)


def test_macd_constant_series_is_zero_after_warmup() -> None:
    result = calculate_macd([7.0] * 34)

    assert result.diff == 0.0
    assert result.dea == 0.0
    assert result.histogram == 0.0


def test_macd_requires_slow_plus_signal_minus_one_closes() -> None:
    with pytest.raises(InsufficientDataError) as exc_info:
        calculate_macd([1.0] * 33)

    assert exc_info.value.required == 34


def test_macd_requires_fast_period_to_be_less_than_slow_period() -> None:
    with pytest.raises(InvalidParameterError):
        calculate_macd([1.0] * 20, fast_period=5, slow_period=5, signal_period=2)
