import pytest

from app.indicators import (
    Candle,
    InsufficientDataError,
    InvalidInputError,
    calculate_kdj,
)


def candles(*rows: tuple[float, float, float]) -> list[Candle]:
    return [Candle(high=high, low=low, close=close) for high, low, close in rows]


def test_kdj_uses_50_seed_and_recursive_k_and_d_smoothing() -> None:
    result = calculate_kdj(
        candles((10, 8, 9), (12, 9, 11), (11, 9, 10)),
        period=2,
        k_period=2,
        d_period=2,
    )

    # RSVs are 75 and 33.333... for the two-period windows.  K/D start at
    # 50 and use (old * (n-1) + new) / n.
    assert result.rsv == pytest.approx(33.3333333333)
    assert result.k == pytest.approx(47.9166666667)
    assert result.d == pytest.approx(52.0833333333)
    assert result.j == pytest.approx(39.5833333333)


def test_kdj_zero_high_low_range_is_neutral_rsv() -> None:
    result = calculate_kdj(
        candles((5, 5, 5), (5, 5, 5), (5, 5, 5)),
        period=3,
    )

    assert result.rsv == 50.0
    assert result.k == 50.0
    assert result.d == 50.0
    assert result.j == 50.0


def test_kdj_requires_high_and_low_for_every_candle() -> None:
    with pytest.raises(InvalidInputError, match="high and low"):
        calculate_kdj([1.0, 2.0, 3.0], period=3)


def test_kdj_rejects_short_series() -> None:
    with pytest.raises(InsufficientDataError):
        calculate_kdj(candles((10, 8, 9), (12, 9, 11)), period=3)
