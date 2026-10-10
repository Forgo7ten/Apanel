"""Seed the canonical persisted v2 indicator format in integration tests."""

from __future__ import annotations

from typing import Any

from app.indicators.parameters import canonicalize_parameters, parameter_key
from app.models import IndicatorSnapshot


def current_snapshot(**payload: Any) -> IndicatorSnapshot:
    """Prepare valid multi-variant rows from concise test inputs.

    This is strictly a fixture factory: production readers do not interpret
    the former flat-row format.
    """
    payload = dict(payload)
    indicator = str(payload["indicator_type"])
    parameters = payload.pop("parameters", {})
    if isinstance(parameters, dict) and parameters.get("_format") == 2:
        payload["parameters"] = parameters
        return IndicatorSnapshot(**payload)
    canonical = canonicalize_parameters(indicator, parameters, fill_defaults=True)
    key = parameter_key(indicator, canonical)
    payload["parameters"] = {"_format": 2, "variants": {key: canonical}}
    for attr in ("values", "previous_values", "delta"):
        value = payload.get(attr)
        payload[attr] = {key: value} if value is not None else None
    return IndicatorSnapshot(**payload)
