"""Bollinger Bands using population standard deviation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .results import BollingerResult
from .types import _finite_float, close_values, normalize_series
from .validation import positive_int, positive_number, require_length


def normalized_band_width(upper: float, lower: float, middle: float) -> float:
    """Return ``(upper - lower) / middle`` with a neutral zero-center rule."""

    upper = _finite_float(upper, field="upper")
    lower = _finite_float(lower, field="lower")
    middle = _finite_float(middle, field="middle")
    if middle == 0.0:
        return 0.0
    width = (upper - lower) / middle
    if not math.isfinite(width):
        raise ValueError("band width must be finite")
    return float(width)


def calculate_bollinger(
    series: Iterable[Any], period: int = 20, multiplier: float = 2.0
) -> BollingerResult:
    period = positive_int("period", period)
    multiplier = positive_number("multiplier", multiplier)
    candles = normalize_series(series)
    require_length(len(candles), period, indicator="boll")
    closes = close_values(candles)
    window = closes[-period:]
    middle = math.fsum(window) / period
    variance = math.fsum((close - middle) ** 2 for close in window) / period
    # Floating point round-off can make a mathematically zero variance tiny
    # negative only in more complex refactors; guard the sqrt boundary.
    standard_deviation = math.sqrt(max(variance, 0.0))
    upper = middle + (multiplier * standard_deviation)
    lower = middle - (multiplier * standard_deviation)
    width = normalized_band_width(upper, lower, middle)
    return BollingerResult(
        upper=float(upper),
        middle=float(middle),
        lower=float(lower),
        width=float(width),
        period=period,
        multiplier=multiplier,
    )


def bollinger(series: Iterable[Any], period: int = 20, multiplier: float = 2.0) -> BollingerResult:
    return calculate_bollinger(series, period, multiplier)


def boll(series: Iterable[Any], period: int = 20, multiplier: float = 2.0) -> BollingerResult:
    return calculate_bollinger(series, period, multiplier)


calculate_boll = calculate_bollinger


class BollingerIndicator:
    name = "boll"

    def __init__(self, period: int = 20, multiplier: float = 2.0) -> None:
        self.period = positive_int("period", period)
        self.multiplier = positive_number("multiplier", multiplier)

    def calculate(
        self,
        series: Iterable[Any],
        *,
        period: int | None = None,
        multiplier: float | None = None,
    ) -> BollingerResult:
        return calculate_bollinger(
            series,
            self.period if period is None else period,
            self.multiplier if multiplier is None else multiplier,
        )


BOLLIndicator = BollingerIndicator
