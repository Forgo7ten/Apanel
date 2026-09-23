"""Immutable, snapshot-friendly indicator results."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar

from .errors import InvalidParameterError, ResultValidationError
from .validation import positive_int, positive_number


class IndicatorResult(ABC):
    """Common public seam for built-in and third-party indicator results."""

    indicator: ClassVar[str]

    @abstractmethod
    def _values(self) -> Mapping[str, float]:
        """Return scalar values suitable for an IndicatorSnapshot."""

    @property
    @abstractmethod
    def parameters(self) -> Mapping[str, int | float]:
        """Return the validated parameters used for this result."""

    def __post_init__(self) -> None:
        for key, value in self._values().items():
            if not isinstance(key, str) or not key:
                raise ResultValidationError("result value keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ResultValidationError(f"result value {key!r} must be numeric")
            if not math.isfinite(float(value)):
                raise ResultValidationError(f"result value {key!r} must be finite")

        for key, value in self.parameters.items():
            if not isinstance(key, str) or not key:
                raise ResultValidationError("result parameter keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ResultValidationError(f"result parameter {key!r} must be numeric")
            if not math.isfinite(float(value)):
                raise ResultValidationError(f"result parameter {key!r} must be finite")

    @property
    def values(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self._values()))

    def to_snapshot(self) -> dict[str, object]:
        """Adapt the pure result to the later IndicatorSnapshot seam."""

        return {
            "indicator": self.indicator,
            "parameters": dict(self.parameters),
            "values": dict(self.values),
        }

    def to_dict(self) -> dict[str, float]:
        """Return only scalar values, useful for API/schema serialization."""

        return dict(self.values)

    def __getitem__(self, key: str) -> float:
        return self.values[key]


@dataclass(frozen=True, slots=True)
class SMAResult(IndicatorResult):
    value: float
    period: int
    indicator: ClassVar[str] = "ma"

    @property
    def parameters(self) -> Mapping[str, int | float]:
        return MappingProxyType({"period": self.period})

    def _values(self) -> Mapping[str, float]:
        return {"value": self.value}

    @property
    def ma(self) -> float:
        return self.value

    @property
    def sma(self) -> float:
        return self.value

    def __post_init__(self) -> None:
        positive_int("period", self.period)
        IndicatorResult.__post_init__(self)


@dataclass(frozen=True, slots=True)
class ProjectedMAResult(IndicatorResult):
    value: float
    period: int
    projected_close: float
    indicator: ClassVar[str] = "projected_ma"

    @property
    def parameters(self) -> Mapping[str, int | float]:
        return MappingProxyType({"period": self.period})

    def _values(self) -> Mapping[str, float]:
        return {"value": self.value, "projected_close": self.projected_close}

    def __post_init__(self) -> None:
        positive_int("period", self.period)
        IndicatorResult.__post_init__(self)


@dataclass(frozen=True, slots=True)
class RSIResult(IndicatorResult):
    value: float
    period: int
    average_gain: float
    average_loss: float
    indicator: ClassVar[str] = "rsi"

    @property
    def parameters(self) -> Mapping[str, int | float]:
        return MappingProxyType({"period": self.period})

    def _values(self) -> Mapping[str, float]:
        return {
            "value": self.value,
            "average_gain": self.average_gain,
            "average_loss": self.average_loss,
        }

    @property
    def rsi(self) -> float:
        return self.value

    def __post_init__(self) -> None:
        positive_int("period", self.period)
        IndicatorResult.__post_init__(self)


@dataclass(frozen=True, slots=True)
class KDJResult(IndicatorResult):
    rsv: float
    k: float
    d: float
    j: float
    period: int
    k_period: int
    d_period: int
    indicator: ClassVar[str] = "kdj"

    @property
    def parameters(self) -> Mapping[str, int | float]:
        return MappingProxyType(
            {"period": self.period, "k_period": self.k_period, "d_period": self.d_period}
        )

    def _values(self) -> Mapping[str, float]:
        return {"rsv": self.rsv, "k": self.k, "d": self.d, "j": self.j}

    @property
    def K(self) -> float:  # noqa: N802 - mirrors the domain notation K/D/J
        return self.k

    @property
    def D(self) -> float:  # noqa: N802 - mirrors the domain notation K/D/J
        return self.d

    @property
    def J(self) -> float:  # noqa: N802 - mirrors the domain notation K/D/J
        return self.j

    def __post_init__(self) -> None:
        positive_int("period", self.period)
        positive_int("k_period", self.k_period)
        positive_int("d_period", self.d_period)
        IndicatorResult.__post_init__(self)


@dataclass(frozen=True, slots=True)
class BollingerResult(IndicatorResult):
    upper: float
    middle: float
    lower: float
    width: float
    period: int
    multiplier: float
    indicator: ClassVar[str] = "boll"

    @property
    def band_width(self) -> float:
        return self.width

    @property
    def middle_band(self) -> float:
        return self.middle

    @property
    def bandwidth(self) -> float:
        return self.width

    @property
    def parameters(self) -> Mapping[str, int | float]:
        return MappingProxyType({"period": self.period, "multiplier": self.multiplier})

    def _values(self) -> Mapping[str, float]:
        return {
            "upper": self.upper,
            "middle": self.middle,
            "lower": self.lower,
            "width": self.width,
        }

    def __post_init__(self) -> None:
        positive_int("period", self.period)
        positive_number("multiplier", self.multiplier)
        IndicatorResult.__post_init__(self)


@dataclass(frozen=True, slots=True)
class MACDResult(IndicatorResult):
    diff: float
    dea: float
    histogram: float
    fast_period: int
    slow_period: int
    signal_period: int
    indicator: ClassVar[str] = "macd"

    @property
    def signal(self) -> float:
        return self.dea

    @property
    def parameters(self) -> Mapping[str, int | float]:
        return MappingProxyType(
            {
                "fast_period": self.fast_period,
                "slow_period": self.slow_period,
                "signal_period": self.signal_period,
            }
        )

    def _values(self) -> Mapping[str, float]:
        return {"diff": self.diff, "dea": self.dea, "histogram": self.histogram}

    def __post_init__(self) -> None:
        positive_int("fast_period", self.fast_period)
        positive_int("slow_period", self.slow_period)
        positive_int("signal_period", self.signal_period)
        if self.fast_period >= self.slow_period:
            raise InvalidParameterError("fast_period must be less than slow_period")
        IndicatorResult.__post_init__(self)
