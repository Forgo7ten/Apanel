"""KDJ oscillator with explicit warm-up and zero-range conventions."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Any

from .results import KDJResult
from .types import normalize_series, require_ohlc
from .validation import positive_int, require_length


def calculate_kdj(
    series: Iterable[Any],
    period: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> KDJResult:
    """Return the latest K/D/J value.

    Before the first complete ``period`` window, no RSV exists.  At that
    point K and D are seeded at 50.  For every subsequent RSV, including the
    first one, ``k_period`` and ``d_period`` are used as Wilder-like smoothing
    divisors.  A zero high/low range has the neutral RSV value 50.
    """

    period = positive_int("period", period)
    k_period = positive_int("k_period", k_period)
    d_period = positive_int("d_period", d_period)
    candles = normalize_series(series)
    require_length(len(candles), period, indicator="kdj")
    require_ohlc(candles)

    k = 50.0
    d = 50.0
    rsv = 50.0
    for index in range(period - 1, len(candles)):
        window = candles[index - period + 1 : index + 1]
        highest = max(candle.high for candle in window if candle.high is not None)
        lowest = min(candle.low for candle in window if candle.low is not None)
        close = candles[index].close
        assert close is not None
        spread = highest - lowest
        rsv = 50.0 if spread == 0.0 else ((close - lowest) / spread) * 100.0
        k = ((k * (k_period - 1)) + rsv) / k_period
        d = ((d * (d_period - 1)) + k) / d_period

    j = (3.0 * k) - (2.0 * d)
    return KDJResult(
        rsv=float(rsv),
        k=float(k),
        d=float(d),
        j=float(j),
        period=period,
        k_period=k_period,
        d_period=d_period,
    )


def kdj(
    series: Iterable[Any],
    period: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> KDJResult:
    return calculate_kdj(series, period, k_period, d_period)


class KDJIndicator:
    name = "kdj"

    def calculate_series(
        self,
        series: Iterable[Any],
        *,
        period: int | None = None,
        k_period: int | None = None,
        d_period: int | None = None,
    ):
        selected = positive_int("period", self.period if period is None else period)
        kp = positive_int("k_period", self.k_period if k_period is None else k_period)
        dp = positive_int("d_period", self.d_period if d_period is None else d_period)
        candles = normalize_series(series)
        require_ohlc(candles)
        output = [None] * len(candles)
        highs: deque[int] = deque()
        lows: deque[int] = deque()
        k = d = 50.0
        for index, candle in enumerate(candles):
            assert candle.high is not None and candle.low is not None and candle.close is not None
            while highs and candles[highs[-1]].high <= candle.high:
                highs.pop()
            highs.append(index)
            while lows and candles[lows[-1]].low >= candle.low:
                lows.pop()
            lows.append(index)
            cutoff = index - selected + 1
            while highs and highs[0] < cutoff:
                highs.popleft()
            while lows and lows[0] < cutoff:
                lows.popleft()
            if index < selected - 1:
                continue
            highest = candles[highs[0]].high
            lowest = candles[lows[0]].low
            assert highest is not None and lowest is not None
            spread = highest - lowest
            rsv = 50.0 if spread == 0.0 else ((candle.close - lowest) / spread) * 100.0
            k = ((k * (kp - 1)) + rsv) / kp
            d = ((d * (dp - 1)) + k) / dp
            output[index] = KDJResult(
                rsv=float(rsv),
                k=float(k),
                d=float(d),
                j=float((3.0 * k) - (2.0 * d)),
                period=selected,
                k_period=kp,
                d_period=dp,
            )
        return tuple(output)

    def __init__(self, period: int = 9, k_period: int = 3, d_period: int = 3) -> None:
        self.period = positive_int("period", period)
        self.k_period = positive_int("k_period", k_period)
        self.d_period = positive_int("d_period", d_period)

    def calculate(
        self,
        series: Iterable[Any],
        *,
        period: int | None = None,
        k_period: int | None = None,
        d_period: int | None = None,
    ) -> KDJResult:
        return calculate_kdj(
            series,
            self.period if period is None else period,
            self.k_period if k_period is None else k_period,
            self.d_period if d_period is None else d_period,
        )
