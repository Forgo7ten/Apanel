"""Moving Average Convergence Divergence with explicit EMA seeds."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .results import MACDResult
from .types import close_values, normalize_series
from .validation import positive_int, require_length


def _ema(values: tuple[float, ...], period: int) -> tuple[float | None, ...]:
    """Return an EMA series seeded by the first period's SMA."""

    seed = math.fsum(values[:period]) / period
    output: list[float | None] = [None] * (period - 1)
    output.append(float(seed))
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for value in values[period:]:
        previous = (alpha * value) + ((1.0 - alpha) * previous)
        output.append(float(previous))
    return tuple(output)


def calculate_macd(
    series: Iterable[Any],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    fast_period = positive_int("fast_period", fast_period)
    slow_period = positive_int("slow_period", slow_period)
    signal_period = positive_int("signal_period", signal_period)
    if fast_period >= slow_period:
        from .errors import InvalidParameterError

        raise InvalidParameterError("fast_period must be less than slow_period")

    candles = normalize_series(series)
    required = slow_period + signal_period - 1
    require_length(len(candles), required, indicator="macd")
    closes = close_values(candles)
    fast_ema = _ema(closes, fast_period)
    slow_ema = _ema(closes, slow_period)

    diffs: list[float] = []
    for index in range(slow_period - 1, len(closes)):
        fast_value = fast_ema[index]
        slow_value = slow_ema[index]
        assert fast_value is not None and slow_value is not None
        diffs.append(float(fast_value - slow_value))

    diff_values = tuple(diffs)
    signal_values = _ema(diff_values, signal_period)
    diff = diff_values[-1]
    dea = signal_values[-1]
    assert dea is not None
    histogram = diff - dea
    return MACDResult(
        diff=float(diff),
        dea=float(dea),
        histogram=float(histogram),
        fast_period=fast_period,
        slow_period=slow_period,
        signal_period=signal_period,
    )


def macd(
    series: Iterable[Any],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    return calculate_macd(series, fast_period, slow_period, signal_period)


class MACDIndicator:
    name = "macd"

    def __init__(
        self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
    ) -> None:
        self.fast_period = positive_int("fast_period", fast_period)
        self.slow_period = positive_int("slow_period", slow_period)
        self.signal_period = positive_int("signal_period", signal_period)

    def calculate(
        self,
        series: Iterable[Any],
        *,
        fast_period: int | None = None,
        slow_period: int | None = None,
        signal_period: int | None = None,
    ) -> MACDResult:
        return calculate_macd(
            series,
            self.fast_period if fast_period is None else fast_period,
            self.slow_period if slow_period is None else slow_period,
            self.signal_period if signal_period is None else signal_period,
        )
