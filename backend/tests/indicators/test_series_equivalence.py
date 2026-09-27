from __future__ import annotations

import pytest

from app.indicators.boll import BollingerIndicator, calculate_bollinger
from app.indicators.kdj import KDJIndicator, calculate_kdj
from app.indicators.ma import (
    ProjectedMAIndicator,
    SMAIndicator,
    calculate_projected_ma,
    calculate_sma,
)
from app.indicators.macd import MACDIndicator, calculate_macd
from app.indicators.rsi import RSIIndicator, calculate_rsi


def candles(count: int = 80):
    return [
        {
            "open": 100 + index * 0.2,
            "high": 101 + index * 0.3,
            "low": 99 + index * 0.1,
            "close": 100 + index * 0.25,
            "volume": 1000 + index,
        }
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("indicator", "legacy", "parameters", "minimum"),
    [
        (SMAIndicator(), calculate_sma, {"period": 5}, 5),
        (ProjectedMAIndicator(), calculate_projected_ma, {"period": 5}, 5),
        (RSIIndicator(), calculate_rsi, {"period": 14}, 15),
        (KDJIndicator(), calculate_kdj, {"period": 9, "k_period": 3, "d_period": 3}, 9),
        (BollingerIndicator(), calculate_bollinger, {"period": 20, "multiplier": 2.0}, 20),
        (
            MACDIndicator(),
            calculate_macd,
            {"fast_period": 12, "slow_period": 26, "signal_period": 9},
            34,
        ),
    ],
)
def test_series_path_matches_prefix_formula(indicator, legacy, parameters, minimum) -> None:
    values = candles()
    series = indicator.calculate_series(values, **parameters)
    for index in range(minimum - 1, len(values)):
        expected = legacy(values[: index + 1], **parameters)
        actual = series[index]
        assert actual is not None
        assert actual.to_dict() == pytest.approx(expected.to_dict())
