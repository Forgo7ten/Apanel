"""Small validation helpers shared by indicator implementations."""

from __future__ import annotations

import math
from decimal import Decimal
from numbers import Integral, Real
from typing import Any

from .errors import InsufficientDataError, InvalidParameterError


def positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise InvalidParameterError(f"{name} must be a positive integer")
    return int(value)


def positive_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise InvalidParameterError(f"{name} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise InvalidParameterError(f"{name} must be a positive finite number")
    return result


def require_length(length: int, required: int, *, indicator: str) -> None:
    if length < required:
        raise InsufficientDataError(required, length, indicator=indicator)
