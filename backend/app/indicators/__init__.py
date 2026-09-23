"""Pure, plugin-oriented technical indicator calculations."""

from .base import Indicator
from .boll import (
    BOLLIndicator,
    BollingerIndicator,
    boll,
    bollinger,
    calculate_boll,
    calculate_bollinger,
    normalized_band_width,
)
from .errors import (
    IndicatorError,
    InsufficientDataError,
    InvalidInputError,
    InvalidParameterError,
    ResultValidationError,
    UnknownIndicatorError,
)
from .kdj import KDJIndicator, calculate_kdj, kdj
from .ma import (
    MAIndicator,
    ProjectedMAIndicator,
    SMAIndicator,
    calculate_ma,
    calculate_projected_ma,
    calculate_sma,
    ma,
    projected_ma,
    sma,
)
from .macd import MACDIndicator, calculate_macd, macd
from .registry import DEFAULT_REGISTRY, IndicatorRegistry, create_default_registry, registry
from .results import (
    BollingerResult,
    IndicatorResult,
    KDJResult,
    MACDResult,
    ProjectedMAResult,
    RSIResult,
    SMAResult,
)
from .rsi import RSIIndicator, calculate_rsi, rsi
from .types import Candle, CandleSeries, normalize_series

__all__ = [
    "Candle",
    "CandleSeries",
    "BOLLIndicator",
    "BollingerIndicator",
    "BollingerResult",
    "DEFAULT_REGISTRY",
    "IndicatorError",
    "Indicator",
    "IndicatorResult",
    "IndicatorRegistry",
    "InsufficientDataError",
    "InvalidInputError",
    "InvalidParameterError",
    "KDJIndicator",
    "KDJResult",
    "MACDIndicator",
    "MACDResult",
    "MAIndicator",
    "ProjectedMAIndicator",
    "ProjectedMAResult",
    "ResultValidationError",
    "RSIIndicator",
    "RSIResult",
    "SMAIndicator",
    "SMAResult",
    "UnknownIndicatorError",
    "boll",
    "bollinger",
    "calculate_bollinger",
    "calculate_boll",
    "calculate_kdj",
    "calculate_macd",
    "calculate_projected_ma",
    "calculate_ma",
    "calculate_rsi",
    "calculate_sma",
    "create_default_registry",
    "kdj",
    "ma",
    "macd",
    "normalize_series",
    "projected_ma",
    "normalized_band_width",
    "registry",
    "rsi",
    "sma",
]
