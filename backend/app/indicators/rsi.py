"""Relative Strength Index using Wilder's smoothed averages."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from .results import RSIResult
from .types import close_values, normalize_series
from .validation import positive_int, require_length


def calculate_rsi(series: Iterable[Any], period: int = 14) -> RSIResult:
    period = positive_int("period", period)
    candles = normalize_series(series)
    require_length(len(candles), period + 1, indicator="rsi")
    closes = close_values(candles)
    changes = tuple(
        current - previous for previous, current in zip(closes, closes[1:], strict=False)
    )

    initial_changes = changes[:period]
    average_gain = math.fsum(max(change, 0.0) for change in initial_changes) / period
    average_loss = math.fsum(max(-change, 0.0) for change in initial_changes) / period

    for change in changes[period:]:
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0.0:
        value = 50.0 if average_gain == 0.0 else 100.0
    elif average_gain == 0.0:
        value = 0.0
    else:
        relative_strength = average_gain / average_loss
        value = 100.0 - (100.0 / (1.0 + relative_strength))

    return RSIResult(
        value=float(value),
        period=period,
        average_gain=float(average_gain),
        average_loss=float(average_loss),
    )


def rsi(series: Iterable[Any], period: int = 14) -> RSIResult:
    return calculate_rsi(series, period)


class RSIIndicator:
    name = "rsi"

    def __init__(self, period: int = 14) -> None:
        self.period = positive_int("period", period)

    def calculate(self, series: Iterable[Any], *, period: int | None = None) -> RSIResult:
        return calculate_rsi(series, self.period if period is None else period)
