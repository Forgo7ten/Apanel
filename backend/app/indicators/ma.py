"""Simple and next-day projected moving averages."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .results import ProjectedMAResult, SMAResult
from .types import close_values, normalize_series
from .validation import positive_int, require_length


def calculate_sma(series: Iterable[Any], period: int = 5) -> SMAResult:
    period = positive_int("period", period)
    candles = normalize_series(series)
    require_length(len(candles), period, indicator="ma")
    closes = close_values(candles)
    value = math.fsum(closes[-period:]) / period
    return SMAResult(value=float(value), period=period)


def calculate_projected_ma(series: Iterable[Any], period: int = 5) -> ProjectedMAResult:
    """Calculate MA for the next session assuming next close equals last close."""

    period = positive_int("period", period)
    candles = normalize_series(series)
    require_length(len(candles), period, indicator="projected_ma")
    closes = close_values(candles)
    projected_close = closes[-1]
    if period == 1:
        projected_window = (projected_close,)
    else:
        projected_window = (*closes[-(period - 1) :], projected_close)
    value = math.fsum(projected_window) / period
    return ProjectedMAResult(
        value=float(value),
        period=period,
        projected_close=float(projected_close),
    )


def sma(series: Iterable[Any], period: int = 5) -> SMAResult:
    return calculate_sma(series, period)


def ma(series: Iterable[Any], period: int = 5) -> SMAResult:
    return calculate_sma(series, period)


calculate_ma = calculate_sma


def projected_ma(series: Iterable[Any], period: int = 5) -> ProjectedMAResult:
    return calculate_projected_ma(series, period)


class SMAIndicator:
    name = "ma"

    def __init__(self, period: int = 5) -> None:
        self.period = positive_int("period", period)

    def calculate(self, series: Iterable[Any], *, period: int | None = None) -> SMAResult:
        return calculate_sma(series, self.period if period is None else period)


MAIndicator = SMAIndicator


class ProjectedMAIndicator:
    name = "projected_ma"

    def __init__(self, period: int = 5) -> None:
        self.period = positive_int("period", period)

    def calculate(self, series: Iterable[Any], *, period: int | None = None) -> ProjectedMAResult:
        return calculate_projected_ma(series, self.period if period is None else period)
