"""Canonical indicator output-field metadata shared by watch and alert services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.errors import ApiError
from app.indicators.parameters import canonicalize_parameters, normalize_indicator_type

_STATIC_FIELDS = {
    "PROJECTED_MA": {"value"},
    "RSI": {"value"},
    "KDJ": {"rsv", "k", "d", "j"},
    "BOLL": {"upper", "middle", "lower", "width"},
    "MACD": {"diff", "dea", "histogram"},
    "DIVIDEND_YIELD": {"value"},
}


def canonical_indicator_field(
    indicator_type: str,
    parameters: Mapping[str, Any] | None,
    field: str | None,
) -> str:
    indicator = normalize_indicator_type(indicator_type)
    if indicator == "DIVIDEND_YIELD":
        if field is None or field.strip().casefold() == "value":
            return "value"
        raise ApiError("INVALID_INDICATOR_FIELD", "Dividend yield only exposes value.", 400)
    params = canonicalize_parameters(indicator, parameters, fill_defaults=True)
    if indicator == "MA":
        periods = params.get("periods")
        if periods is None:
            periods = [params.get("period")]
        allowed = {f"MA{int(period)}" for period in periods if period is not None}
        if field is None and len(allowed) == 1:
            return next(iter(allowed))
        candidate = str(field or "").strip().upper()
        if candidate in allowed:
            return candidate
        raise ApiError(
            "INVALID_INDICATOR_FIELD", "MA alert requires a specific MA period field.", 400
        )
    allowed = _STATIC_FIELDS.get(indicator)
    if allowed is None:
        raise ApiError("INVALID_INDICATOR", "Indicator is not supported.", 400)
    if field is None and len(allowed) == 1:
        return next(iter(allowed))
    candidate = str(field or "").strip().casefold()
    if candidate in allowed:
        return candidate
    raise ApiError("INVALID_INDICATOR_FIELD", "Indicator field is invalid.", 400)


def indicator_fields(
    indicator_type: str, parameters: Mapping[str, Any] | None = None
) -> tuple[str, ...]:
    indicator = normalize_indicator_type(indicator_type)
    if indicator == "MA":
        params = canonicalize_parameters(indicator, parameters, fill_defaults=True)
        periods = params.get("periods") or [params.get("period")]
        return tuple(f"MA{int(period)}" for period in periods if period is not None)
    return tuple(sorted(_STATIC_FIELDS.get(indicator, ())))
