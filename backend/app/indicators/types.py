"""Input types and normalization for the indicator engine.

The engine deliberately normalizes every accepted numeric value to ``float`` at
the boundary.  This gives Decimal and float callers one arithmetic policy and
keeps every result JSON/snapshot friendly.  Non-finite values are rejected
instead of being allowed to contaminate a later calculation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from numbers import Real
from typing import Any

from .errors import InvalidInputError

Numeric = int | float | Decimal


def _finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise InvalidInputError(f"{field} must be a real number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidInputError(f"{field} must be a real number") from exc
    if not math.isfinite(result):
        raise InvalidInputError(f"{field} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class Candle:
    """One completed OHLC candle.

    ``close`` is required for every indicator.  Open/high/low are optional so
    close-only indicators can use the same type; KDJ explicitly requires high
    and low.  Prices, when supplied, must be positive.  A candle is immutable,
    so calculations cannot mutate a caller's input sequence through it.
    """

    open: Numeric | None = None
    high: Numeric | None = None
    low: Numeric | None = None
    close: Numeric | None = None
    volume: Numeric | None = None
    timestamp: Any = None

    def __post_init__(self) -> None:
        if self.close is None:
            raise InvalidInputError("close is required")

        normalized: dict[str, float | None] = {}
        for field in ("open", "high", "low", "close"):
            raw = getattr(self, field)
            if raw is None:
                normalized[field] = None
                continue
            number = _finite_float(raw, field=field)
            if number <= 0:
                raise InvalidInputError(f"{field} must be greater than zero")
            normalized[field] = number

        if normalized["high"] is not None and normalized["low"] is not None:
            if normalized["high"] < normalized["low"]:
                raise InvalidInputError("high must be greater than or equal to low")
            close = normalized["close"]
            assert close is not None
            if close < normalized["low"] or close > normalized["high"]:
                raise InvalidInputError("close must be within the low/high range")
            open_price = normalized["open"]
            if open_price is not None and (
                open_price < normalized["low"] or open_price > normalized["high"]
            ):
                raise InvalidInputError("open must be within the low/high range")

        volume: float | None = None
        if self.volume is not None:
            volume = _finite_float(self.volume, field="volume")
            if volume < 0:
                raise InvalidInputError("volume must not be negative")

        for field, value in normalized.items():
            object.__setattr__(self, field, value)
        object.__setattr__(self, "volume", volume)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Candle:
        allowed = {"open", "high", "low", "close", "volume", "timestamp"}
        unknown = set(value) - allowed
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            raise InvalidInputError(f"unknown candle fields: {names}")
        return cls(**dict(value))


CandleSeries = tuple[Candle, ...]


def normalize_series(series: Iterable[Any]) -> tuple[Candle, ...]:
    """Copy and validate a candle/close series without changing the input."""

    if series is None or isinstance(series, (str, bytes, bytearray)):
        raise InvalidInputError("series must be an iterable of candles or prices")
    try:
        items = tuple(series)
    except TypeError as exc:
        raise InvalidInputError("series must be an iterable of candles or prices") from exc

    normalized: list[Candle] = []
    for index, item in enumerate(items):
        try:
            if isinstance(item, Candle):
                candle = item
            elif isinstance(item, Mapping):
                candle = Candle.from_mapping(item)
            else:
                candle = Candle(close=item)
        except InvalidInputError as exc:
            raise InvalidInputError(f"invalid candle at index {index}: {exc}") from exc
        normalized.append(candle)
    return tuple(normalized)


def require_ohlc(candles: Sequence[Candle]) -> None:
    for index, candle in enumerate(candles):
        if candle.high is None or candle.low is None:
            raise InvalidInputError(f"candle at index {index} requires high and low for KDJ")


def close_values(candles: Sequence[Candle]) -> tuple[float, ...]:
    return tuple(candle.close for candle in candles if candle.close is not None)
