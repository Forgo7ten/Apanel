"""Pure, registry-driven indicator state recognition."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .definitions import StateDefinition, StateSignal
from .errors import InvalidSnapshotError, MissingStateValues
from .registry import DEFAULT_REGISTRY, StateRegistry
from .types import (
    IndicatorSnapshot,
    PriceSnapshot,
    StateEvaluation,
    StateResult,
)

DEFAULT_TOLERANCE = 1e-9


def _validate_tolerance(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("tolerance must be a finite non-negative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("tolerance must be a finite non-negative number")
    return result


def _as_snapshot(value: Any) -> IndicatorSnapshot:
    if isinstance(value, IndicatorSnapshot):
        return value
    if isinstance(value, Mapping):
        return IndicatorSnapshot.from_mapping(value)
    if getattr(value, "indicator", None) is not None and getattr(value, "values", None) is not None:
        return IndicatorSnapshot.from_result(value)
    raise InvalidSnapshotError("snapshot must be IndicatorSnapshot, IndicatorResult, or mapping")


def _as_price(value: Any, previous: Any | None = None) -> PriceSnapshot | None:
    if value is None and previous is None:
        return None
    if isinstance(value, PriceSnapshot):
        if previous is None:
            return value
        return PriceSnapshot(value.current, previous)
    if isinstance(value, Mapping):
        current = value.get("current", value.get("value", value.get("close")))
        prior = value.get("previous", value.get("previous_value"))
        if previous is not None:
            prior = previous
        if current is None:
            raise InvalidSnapshotError("price.current is required")
        return PriceSnapshot(current, prior)
    if value is None:
        raise InvalidSnapshotError("price.current is required")
    return PriceSnapshot(value, previous)


def _reason_for_exception(error: Exception) -> str:
    if isinstance(error, MissingStateValues):
        return error.reason
    if isinstance(error, InvalidSnapshotError):
        message = str(error)
        if "metadata" in message:
            return "invalid_metadata"
        return "invalid_values"
    if isinstance(error, TypeError) and "metadata" in str(error):
        return "invalid_metadata"
    return "invalid_values"


class StateEngine:
    """Evaluate registered states and apply edge-trigger semantics.

    The engine keeps only the last active bit for each ``stream_id``/state
    pair.  This is a small in-process edge seam, not persistence: callers that
    need durable alert state can persist ``StateResult.active`` and pass it as
    ``previous_active`` on the next invocation.  Without an explicit stream
    identifier the state code itself is used, which is convenient for pure
    single-security jobs and can be isolated with ``reset`` between jobs.
    """

    def __init__(
        self,
        registry: StateRegistry | None = None,
        *,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> None:
        self.registry = DEFAULT_REGISTRY if registry is None else registry
        self.tolerance = _validate_tolerance(tolerance)
        self._last_active: dict[tuple[str, str], bool] = {}

    def reset(self, stream_id: str | None = None) -> None:
        """Forget in-process edge history for one stream or all streams."""

        if stream_id is None:
            self._last_active.clear()
            return
        prefix = str(stream_id)
        for key in tuple(self._last_active):
            if key[0] == prefix:
                del self._last_active[key]

    def evaluate_state(
        self,
        code: str,
        current: Any,
        previous: Any | None = None,
        *,
        price: Any | None = None,
        current_price: Any | None = None,
        previous_price: Any | None = None,
        tolerance: float | None = None,
        stream_id: str | None = None,
        security_id: str | None = None,
        symbol: str | None = None,
        previous_active: bool | None = None,
    ) -> StateResult:
        """Evaluate one state at the UI/Alert shared seam.

        A missing previous snapshot is intentionally an inactive result: no
        edge can be proven from one completed trading day.  Invalid snapshot
        metadata/values likewise become an inactive result with a reason,
        allowing a scheduler to observe bad provider data without emitting an
        alert.
        """

        definition = self.registry.get(code)
        resolved_tolerance = self.tolerance if tolerance is None else _validate_tolerance(tolerance)
        resolved_stream = stream_id or security_id or symbol or definition.code
        key = (str(resolved_stream), definition.code)
        try:
            current_snapshot = _as_snapshot(current)
        except (InvalidSnapshotError, TypeError, ValueError) as error:
            return self._inactive(definition, _reason_for_exception(error), key)

        if previous is None:
            return self._inactive(definition, "missing_previous", key)
        try:
            previous_snapshot = _as_snapshot(previous)
        except (InvalidSnapshotError, TypeError, ValueError) as error:
            return self._inactive(definition, _reason_for_exception(error), key)

        if current_snapshot.indicator != definition.indicator_type:
            return self._inactive(definition, "indicator_mismatch", key)
        if previous_snapshot.indicator != definition.indicator_type:
            return self._inactive(definition, "indicator_mismatch", key)

        resolved_price_input = current_price if current_price is not None else price
        try:
            resolved_price = _as_price(resolved_price_input, previous_price)
            signal = definition.evaluator(
                current_snapshot,
                previous_snapshot,
                price=resolved_price,
                previous_price=(resolved_price.previous if resolved_price is not None else None),
                tolerance=resolved_tolerance,
            )
        except (MissingStateValues, InvalidSnapshotError, TypeError, ValueError) as error:
            return self._inactive(definition, _reason_for_exception(error), key)

        if isinstance(signal, StateSignal):
            active = bool(signal.active)
            edge = bool(signal.edge)
        elif isinstance(signal, bool):
            active = signal
            edge = signal
        else:
            return self._inactive(definition, "invalid_evaluator_result", key)

        was_active = previous_active
        if was_active is None:
            was_active = self._last_active.get(key)
        if was_active is None:
            was_active = self._infer_previous_active(
                definition, previous_snapshot, resolved_tolerance
            )

        transition = bool(active and edge and not was_active)
        self._last_active[key] = active
        return StateResult(
            definition=definition,
            active=active,
            transition=transition,
            reason=None,
            values=current_snapshot.values,
        )

    def evaluate(
        self,
        current: Any,
        previous: Any | None = None,
        *,
        price: Any | None = None,
        current_price: Any | None = None,
        previous_price: Any | None = None,
        tolerance: float | None = None,
        state_codes: Sequence[str] | None = None,
        stream_id: str | None = None,
        security_id: str | None = None,
        symbol: str | None = None,
    ) -> tuple[StateEvaluation, ...]:
        """Evaluate all matching states, or an explicit subset, in registry order."""

        # A mapping keyed by indicator type is accepted for one scheduler pass
        # over multiple indicators.  A normal snapshot mapping has envelope
        # keys and is handled as one snapshot below.
        if isinstance(current, Mapping) and not self._looks_like_snapshot(current):
            previous_by_indicator = previous if isinstance(previous, Mapping) else {}
            results: list[StateEvaluation] = []
            for indicator, snapshot_value in current.items():
                prior_value = previous_by_indicator.get(indicator)
                if prior_value is None and isinstance(indicator, str):
                    prior_value = next(
                        (
                            value
                            for key, value in previous_by_indicator.items()
                            if isinstance(key, str) and key.upper() == indicator.upper()
                        ),
                        None,
                    )
                if isinstance(snapshot_value, Mapping) and not self._looks_like_snapshot(
                    snapshot_value
                ):
                    snapshot_value = {
                        "indicator": indicator,
                        "values": snapshot_value,
                    }
                if isinstance(prior_value, Mapping) and not self._looks_like_snapshot(prior_value):
                    prior_value = {
                        "indicator": indicator,
                        "values": prior_value,
                    }
                results.extend(
                    self.evaluate(
                        snapshot_value,
                        prior_value,
                        price=price,
                        current_price=current_price,
                        previous_price=previous_price,
                        tolerance=tolerance,
                        state_codes=state_codes,
                        stream_id=stream_id,
                        security_id=security_id,
                        symbol=symbol,
                    )
                )
            return tuple(results)

        try:
            current_snapshot = _as_snapshot(current)
        except (InvalidSnapshotError, TypeError, ValueError):
            # There is no indicator type with which to select a default set;
            # explicit codes still produce useful per-state invalid results.
            codes = self.registry.codes() if state_codes is None else tuple(state_codes)
            return tuple(
                self.evaluate_state(
                    code,
                    current,
                    previous,
                    price=price,
                    current_price=current_price,
                    previous_price=previous_price,
                    tolerance=tolerance,
                    stream_id=stream_id,
                    security_id=security_id,
                    symbol=symbol,
                )
                for code in codes
            )

        if state_codes is None:
            definitions = tuple(
                definition
                for definition in self.registry.definitions()
                if definition.indicator_type == current_snapshot.indicator
            )
            codes = tuple(definition.code for definition in definitions)
        else:
            codes = tuple(state_codes)
        return tuple(
            self.evaluate_state(
                code,
                current_snapshot,
                previous,
                price=price,
                current_price=current_price,
                previous_price=previous_price,
                tolerance=tolerance,
                stream_id=stream_id,
                security_id=security_id,
                symbol=symbol,
            )
            for code in codes
        )

    # Friendly aliases for service-layer callers that use the product term
    # “recognize”; both routes share exactly the same seam and semantics.
    recognize = evaluate
    recognize_state = evaluate_state

    @staticmethod
    def _looks_like_snapshot(value: Mapping[str, Any]) -> bool:
        return bool({"indicator", "indicator_type", "type", "values"}.intersection(value.keys()))

    def _inactive(
        self,
        definition: StateDefinition,
        reason: str,
        key: tuple[str, str],
    ) -> StateResult:
        self._last_active[key] = False
        return StateResult(
            definition=definition,
            active=False,
            transition=False,
            reason=reason,
            values={},
        )

    @staticmethod
    def _infer_previous_active(
        definition: StateDefinition,
        previous: IndicatorSnapshot,
        tolerance: float,
    ) -> bool:
        """Infer yesterday's continuous state when the snapshot carries history."""

        if previous.previous_values is None:
            return False
        try:
            prior = IndicatorSnapshot(previous.indicator, previous.previous_values)
            signal = definition.evaluator(
                previous,
                prior,
                price=None,
                previous_price=None,
                tolerance=tolerance,
            )
        except (MissingStateValues, InvalidSnapshotError, TypeError, ValueError):
            return False
        if isinstance(signal, StateSignal):
            return signal.active
        return bool(signal) if isinstance(signal, bool) else False


StateRecognizer = StateEngine
