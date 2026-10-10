"""Canonical indicator parameter and snapshot-variant contracts.

Indicator formulas are plugins, but parameter identity is a shared persistence
concern.  Keeping it here prevents the watch table, scheduled calculation,
state engine and alert evaluator from each inventing a slightly different
meaning for the same JSON payload.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.errors import ApiError

DEFAULT_MA_PERIODS = (5, 10)
DEFAULT_PROJECTED_MA_PERIOD = 5
DEFAULT_RSI_PERIOD = 14
DEFAULT_KDJ_PERIODS = {"period": 9, "k_period": 3, "d_period": 3}
DEFAULT_BOLL_PARAMETERS = {"period": 20, "multiplier": 2.0}
DEFAULT_MACD_PARAMETERS = {"fast_period": 12, "slow_period": 26, "signal_period": 9}
MAX_INDICATOR_PERIOD = 10_000
MAX_MA_PERIODS = 32

INDICATOR_ALIASES = {
    "SMA": "MA",
    "PMA": "PROJECTED_MA",
    "PROJECTEDMA": "PROJECTED_MA",
    "BOLLINGER": "BOLL",
}

INDICATOR_PARAMETER_KEYS = {
    "MA": {"period", "periods"},
    "PROJECTED_MA": {"period"},
    "RSI": {"period"},
    "KDJ": {"period", "k_period", "d_period"},
    "BOLL": {"period", "multiplier"},
    "MACD": {"fast_period", "slow_period", "signal_period"},
}

INTEGER_PARAMETER_KEYS = {
    "period",
    "periods",
    "k_period",
    "d_period",
    "fast_period",
    "slow_period",
    "signal_period",
}

DEFAULT_PARAMETER_MAP: dict[str, dict[str, Any]] = {
    "MA": {"periods": list(DEFAULT_MA_PERIODS)},
    "PROJECTED_MA": {"period": DEFAULT_PROJECTED_MA_PERIOD},
    "RSI": {"period": DEFAULT_RSI_PERIOD},
    "KDJ": dict(DEFAULT_KDJ_PERIODS),
    "BOLL": dict(DEFAULT_BOLL_PARAMETERS),
    "MACD": dict(DEFAULT_MACD_PARAMETERS),
}


@dataclass(frozen=True, slots=True)
class IndicatorRequest:
    """One calculation request after canonicalization."""

    indicator_type: str
    parameters: dict[str, Any]

    @property
    def key(self) -> str:
        return parameter_key(self.indicator_type, self.parameters)


@dataclass(frozen=True, slots=True)
class IndicatorVariant:
    """One logical parameterized result from the current snapshot envelope."""

    indicator_type: str
    key: str
    parameters: dict[str, Any]
    values: dict[str, float]
    previous_values: dict[str, float] | None
    delta: dict[str, float] | None


def normalize_indicator_type(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError("INVALID_INDICATOR_PARAMETERS", "Indicator type is required.", 400)
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    return INDICATOR_ALIASES.get(normalized, normalized)


def default_parameters(indicator_type: str) -> dict[str, Any]:
    normalized = normalize_indicator_type(indicator_type)
    return _copy_json(DEFAULT_PARAMETER_MAP.get(normalized, {}))


def canonicalize_parameters(
    indicator_type: str,
    parameters: Mapping[str, Any] | None,
    *,
    fill_defaults: bool = True,
) -> dict[str, Any]:
    """Normalize aliases, numeric representations and ordering.

    ``fill_defaults`` is true at user/API boundaries and false when preserving
    an explicitly persisted v2 variant.  The returned mapping is JSON-safe
    and has deterministic list ordering for MA periods.
    """

    normalized_type = normalize_indicator_type(indicator_type)
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise ApiError(
            "INVALID_INDICATOR_PARAMETERS",
            "Indicator parameters must be an object.",
            400,
        )

    raw: dict[str, Any] = {}
    for key, value in parameters.items():
        if not isinstance(key, str) or not key.strip():
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                "Indicator parameter names must be non-empty strings.",
                400,
            )
        normalized_key = key.strip().lower()
        if normalized_type == "BOLL" and normalized_key in {"std", "stddev", "std_dev"}:
            normalized_key = "multiplier"
        if normalized_key in raw and raw[normalized_key] != value:
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                "Indicator parameters contain duplicate names.",
                400,
            )
        raw[normalized_key] = value

    allowed = INDICATOR_PARAMETER_KEYS.get(normalized_type)
    if allowed is not None:
        unexpected = set(raw) - allowed
        if unexpected:
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                "Indicator parameters contain unsupported names.",
                400,
            )

    if normalized_type == "MA":
        if "period" in raw and "periods" in raw:
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                "MA accepts either period or periods, not both.",
                400,
            )
        if "period" in raw:
            return {"period": _normalize_parameter_value("period", raw["period"])}
        if "periods" in raw:
            raw_periods = raw["periods"]
            if not isinstance(raw_periods, (list, tuple, set, frozenset)) or not raw_periods:
                raise ApiError(
                    "INVALID_INDICATOR_PARAMETERS",
                    "MA periods must be a non-empty list.",
                    400,
                )
            periods = sorted(
                {_normalize_parameter_value("period", period) for period in raw_periods}
            )
            if len(periods) > MAX_MA_PERIODS:
                raise ApiError(
                    "INVALID_INDICATOR_PARAMETERS",
                    f"MA accepts at most {MAX_MA_PERIODS} distinct periods.",
                    400,
                )
            return {"periods": periods}
        return default_parameters(normalized_type) if fill_defaults else {}

    normalized = default_parameters(normalized_type) if fill_defaults else {}
    for key, value in raw.items():
        if normalized_type in INDICATOR_PARAMETER_KEYS:
            normalized[key] = _normalize_parameter_value(key, value)
        else:
            normalized[key] = _normalize_json_value(value)
    if normalized_type == "MACD" and {
        "fast_period",
        "slow_period",
    }.issubset(normalized):
        if normalized["fast_period"] >= normalized["slow_period"]:
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                "MACD fast_period must be less than slow_period.",
                400,
            )
    return {key: normalized[key] for key in sorted(normalized)}


def canonicalize_state_parameters(
    indicator_type: str, parameters: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Shared Watch/Alert identity; MA state periods obey normal indicator limits."""
    indicator = normalize_indicator_type(indicator_type)
    if indicator != "MA":
        return canonicalize_parameters(indicator, parameters, fill_defaults=True)
    raw = dict(parameters or {})
    if set(raw) - {"short", "long", "short_period", "long_period"}:
        raise ApiError("INVALID_STATE_PARAMETERS", "Unsupported MA state parameters.", 400)
    result: dict[str, Any] = {}
    for name, default in zip(("short", "long"), DEFAULT_MA_PERIODS, strict=True):
        value = raw.get(name + "_period", raw.get(name, default))
        if name in raw and name + "_period" in raw and raw[name] != value:
            raise ApiError("INVALID_STATE_PARAMETERS", "MA state aliases conflict.", 400)
        result[name + "_period"] = _normalize_parameter_value("period", value)
    if result["short_period"] >= result["long_period"]:
        raise ApiError("INVALID_STATE_PARAMETERS", "MA short period must precede long period.", 400)
    return result


def state_indicator_request(indicator_type: str, parameters: Mapping[str, Any]) -> IndicatorRequest:
    normalized = canonicalize_state_parameters(indicator_type, parameters)
    if normalize_indicator_type(indicator_type) == "MA":
        normalized = {"periods": [normalized["short_period"], normalized["long_period"]]}
    return IndicatorRequest(normalize_indicator_type(indicator_type), normalized)


def parameter_key(indicator_type: str, parameters: Mapping[str, Any] | None) -> str:
    """Return a versioned, content-addressed identity for one parameter set."""

    normalized_type = normalize_indicator_type(indicator_type)
    canonical = canonicalize_parameters(normalized_type, parameters, fill_defaults=False)
    if normalized_type == "MA" and "period" in canonical:
        canonical = {"periods": [canonical["period"]]}
    encoded = json.dumps(
        {"indicator_type": normalized_type, "parameters": canonical},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"v1_{hashlib.sha256(encoded).hexdigest()}"


def merge_indicator_requests(
    requests: Iterable[IndicatorRequest | tuple[str, Mapping[str, Any]] | Mapping[str, Any]] | None,
) -> tuple[IndicatorRequest, ...]:
    """Merge defaults with table/user requests and deduplicate by canonical key."""

    raw_requests: list[IndicatorRequest] = [
        IndicatorRequest(indicator_type, canonicalize_parameters(indicator_type, parameters))
        for indicator_type, parameters in _default_request_pairs()
    ]
    if requests is not None:
        raw_requests.extend(_coerce_requests(requests))

    ma_requests: dict[tuple[int, ...], IndicatorRequest] = {}
    others: dict[tuple[str, str], IndicatorRequest] = {}
    for request in raw_requests:
        indicator_type = normalize_indicator_type(request.indicator_type)
        parameters = canonicalize_parameters(indicator_type, request.parameters)
        if indicator_type == "MA":
            periods = tuple(sorted(set(_ma_periods(parameters))))
            normalized = IndicatorRequest("MA", {"periods": list(periods)})
            ma_requests[periods] = normalized
            continue
        normalized = IndicatorRequest(indicator_type, parameters)
        others[(indicator_type, normalized.key)] = normalized

    # Keep exact MA requests as independent variants instead of collapsing
    # every user's periods into one global bundle.  Do not remove subset
    # variants: a two-period request may come from an explicit MA state alert
    # and must survive even when a wider numeric MA column contains both
    # periods.  StateService decides which explicit variants produce states,
    # avoiding the old O(n^2) cross-product across unrelated numeric columns.
    merged: list[IndicatorRequest] = sorted(ma_requests.values(), key=lambda item: item.key)
    merged.extend(sorted(others.values(), key=lambda item: (item.indicator_type, item.key)))
    return tuple(merged)


def required_history_bars(
    requests: Iterable[IndicatorRequest | tuple[str, Mapping[str, Any]] | Mapping[str, Any]] | None,
) -> int:
    """Return the minimum completed daily-bar count needed by requested indicators."""

    required = 1
    for request in merge_indicator_requests(requests):
        indicator_type = normalize_indicator_type(request.indicator_type)
        parameters = canonicalize_parameters(indicator_type, request.parameters, fill_defaults=True)
        if indicator_type == "MA":
            periods = parameters.get("periods") or [parameters.get("period", 1)]
            required = max(required, *(int(period) for period in periods if period is not None))
        elif indicator_type == "PROJECTED_MA":
            required = max(required, int(parameters.get("period", DEFAULT_PROJECTED_MA_PERIOD)))
        elif indicator_type == "RSI":
            required = max(required, int(parameters.get("period", DEFAULT_RSI_PERIOD)) + 1)
        elif indicator_type == "KDJ":
            required = max(required, int(parameters.get("period", DEFAULT_KDJ_PERIODS["period"])))
        elif indicator_type == "BOLL":
            required = max(
                required, int(parameters.get("period", DEFAULT_BOLL_PARAMETERS["period"]))
            )
        elif indicator_type == "MACD":
            required = max(
                required,
                int(parameters.get("slow_period", DEFAULT_MACD_PARAMETERS["slow_period"]))
                + int(parameters.get("signal_period", DEFAULT_MACD_PARAMETERS["signal_period"]))
                - 1,
            )
    return required


def snapshot_variants(snapshot: Any) -> tuple[IndicatorVariant, ...]:
    """Read only the canonical multi-variant snapshot format.

    A flat historic snapshot is not interpreted as a modern observation:
    accepting it silently could align a value with the wrong parameters.
    """

    indicator_type = normalize_indicator_type(snapshot.indicator_type)
    parameters = snapshot.parameters if isinstance(snapshot.parameters, Mapping) else {}
    values = snapshot.values if isinstance(snapshot.values, Mapping) else {}
    previous_values = (
        snapshot.previous_values if isinstance(snapshot.previous_values, Mapping) else None
    )
    delta = snapshot.delta if isinstance(snapshot.delta, Mapping) else None
    if parameters.get("_format") != 2 or not isinstance(parameters.get("variants"), Mapping):
        raise ApiError("UNSUPPORTED_SNAPSHOT_FORMAT", "Indicator snapshot format is invalid.", 409)
    result: list[IndicatorVariant] = []
    for raw_key, raw_parameters in parameters["variants"].items():
        normalized_parameters = canonicalize_parameters(
            indicator_type, raw_parameters, fill_defaults=False
        )
        key = parameter_key(indicator_type, normalized_parameters)
        if raw_key != key or not isinstance(values.get(key), Mapping):
            raise ApiError(
                "INVALID_SNAPSHOT_VARIANT", "Indicator snapshot variant is inconsistent.", 409
            )
        prior_map = previous_values.get(key) if previous_values else None
        delta_map = delta.get(key) if delta else None
        result.append(
            IndicatorVariant(
                indicator_type,
                key,
                normalized_parameters,
                _float_mapping(values[key]),
                _float_mapping(prior_map) if isinstance(prior_map, Mapping) else None,
                _float_mapping(delta_map) if isinstance(delta_map, Mapping) else None,
            )
        )
    return tuple(result)


def select_snapshot_variant(
    snapshot: Any,
    indicator_type: str,
    parameters: Mapping[str, Any] | None = None,
) -> IndicatorVariant | None:
    """Select exact parameters, or a containing MA bundle, from one row."""

    normalized_type = normalize_indicator_type(indicator_type)
    requested = canonicalize_parameters(normalized_type, parameters, fill_defaults=True)
    requested_key = parameter_key(normalized_type, requested)
    variants = snapshot_variants(snapshot)
    for variant in variants:
        if variant.key == requested_key or variant.parameters == requested:
            return variant
    if normalized_type == "MA":
        requested_periods = set(_ma_periods(requested))
        for variant in variants:
            candidate_periods = set(_ma_periods(variant.parameters))
            if requested_periods and requested_periods.issubset(candidate_periods):
                return _project_ma_variant(variant, requested_periods)
    return None


def parameters_match(
    indicator_type: str,
    requested: Mapping[str, Any] | None,
    stored: Mapping[str, Any] | None,
) -> bool:
    """Compare user parameters to a stored variant, including MA bundles."""

    normalized_type = normalize_indicator_type(indicator_type)
    requested_parameters = canonicalize_parameters(normalized_type, requested, fill_defaults=True)
    stored_parameters = canonicalize_parameters(normalized_type, stored, fill_defaults=True)
    if normalized_type != "MA":
        return requested_parameters == stored_parameters
    return set(_ma_periods(requested_parameters)).issubset(set(_ma_periods(stored_parameters)))


def _project_ma_variant(variant: IndicatorVariant, periods: set[int]) -> IndicatorVariant:
    values = {
        key: value for key, value in variant.values.items() if _ma_value_period(key) in periods
    }
    previous = (
        {
            key: value
            for key, value in variant.previous_values.items()
            if _ma_value_period(key) in periods
        }
        if variant.previous_values is not None
        else None
    )
    delta = (
        {key: value for key, value in variant.delta.items() if _ma_value_period(key) in periods}
        if variant.delta is not None
        else None
    )
    return IndicatorVariant(
        variant.indicator_type,
        parameter_key("MA", {"periods": sorted(periods)}),
        {"periods": sorted(periods)},
        values,
        previous,
        delta,
    )


def _ma_periods(parameters: Mapping[str, Any]) -> tuple[int, ...]:
    if "periods" in parameters:
        return tuple(int(period) for period in parameters["periods"])
    if "period" in parameters:
        return (int(parameters["period"]),)
    return ()


def _ma_value_period(key: str) -> int | None:
    if not isinstance(key, str) or not key.upper().startswith("MA"):
        return None
    suffix = key[2:]
    return int(suffix) if suffix.isdigit() else None


def _default_request_pairs() -> tuple[tuple[str, Mapping[str, Any]], ...]:
    return tuple((key, default_parameters(key)) for key in DEFAULT_PARAMETER_MAP)


def _coerce_requests(
    requests: Iterable[IndicatorRequest | tuple[str, Mapping[str, Any]] | Mapping[str, Any]],
) -> list[IndicatorRequest]:
    result: list[IndicatorRequest] = []
    for raw in requests:
        if isinstance(raw, IndicatorRequest):
            result.append(raw)
            continue
        if isinstance(raw, tuple) and len(raw) == 2:
            result.append(IndicatorRequest(str(raw[0]), dict(raw[1])))
            continue
        if isinstance(raw, Mapping):
            if "indicator_type" in raw:
                result.append(
                    IndicatorRequest(
                        str(raw["indicator_type"]),
                        dict(raw.get("parameters") or {}),
                    )
                )
                continue
            for indicator_type, parameters in raw.items():
                if isinstance(parameters, list) and all(
                    isinstance(item, Mapping) for item in parameters
                ):
                    result.extend(
                        IndicatorRequest(str(indicator_type), dict(item)) for item in parameters
                    )
                else:
                    result.append(IndicatorRequest(str(indicator_type), dict(parameters or {})))
            continue
        raise TypeError("indicator requests must be IndicatorRequest, pairs, or mappings")
    return result


def _normalize_parameter_value(key: str, value: Any) -> int | float:
    if key in INTEGER_PARAMETER_KEYS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                f"Indicator parameter {key} must be a positive integer.",
                400,
            )
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                f"Indicator parameter {key} must be a positive integer.",
                400,
            )
        if value <= 0:
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                f"Indicator parameter {key} must be a positive integer.",
                400,
            )
        normalized = int(value)
        if normalized > MAX_INDICATOR_PERIOD:
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                f"Indicator parameter {key} must not exceed {MAX_INDICATOR_PERIOD}.",
                400,
            )
        return normalized
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(
            "INVALID_INDICATOR_PARAMETERS",
            f"Indicator parameter {key} must be numeric.",
            400,
        )
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ApiError(
            "INVALID_INDICATOR_PARAMETERS",
            f"Indicator parameter {key} must be positive and finite.",
            400,
        )
    return normalized


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ApiError(
                "INVALID_INDICATOR_PARAMETERS",
                "Indicator parameters must contain finite numbers.",
                400,
            )
        return value
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_value(item) for key, item in sorted(value.items())}
    raise ApiError(
        "INVALID_INDICATOR_PARAMETERS",
        "Indicator parameters contain an unsupported value.",
        400,
    )


def normalize_json_parameters(value: Any) -> dict[str, Any]:
    """Validate a non-indicator JSON parameter object at the API boundary."""

    if not isinstance(value, Mapping):
        raise ApiError(
            "INVALID_INDICATOR_PARAMETERS",
            "Indicator parameters must be an object.",
            400,
        )
    normalized = _normalize_json_value(value)
    assert isinstance(normalized, dict)
    return normalized


def _float_mapping(value: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): float(item) for key, item in value.items()}


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    return value


__all__ = [
    "DEFAULT_BOLL_PARAMETERS",
    "DEFAULT_KDJ_PERIODS",
    "DEFAULT_MACD_PARAMETERS",
    "DEFAULT_MA_PERIODS",
    "DEFAULT_PARAMETER_MAP",
    "DEFAULT_PROJECTED_MA_PERIOD",
    "DEFAULT_RSI_PERIOD",
    "IndicatorRequest",
    "IndicatorVariant",
    "canonicalize_parameters",
    "default_parameters",
    "merge_indicator_requests",
    "normalize_json_parameters",
    "normalize_indicator_type",
    "parameter_key",
    "parameters_match",
    "select_snapshot_variant",
    "snapshot_variants",
]
