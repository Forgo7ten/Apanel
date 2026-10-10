"""Resolve one precise observation target for an alert rule."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators.parameters import select_snapshot_variant
from app.models import AlertRule, IndicatorSnapshot
from app.repositories.alert import AlertRepository
from app.repositories.security import SecurityRepository
from app.schemas.state_row import StateRow
from app.services.analysis_control import publication_is_current, published_stream_for_read
from app.services.dividend_service import DividendYieldService
from app.states import DEFAULT_REGISTRY, StateRegistry, StateStatus


@dataclass(frozen=True, slots=True)
class ScalarObservation:
    value: float
    previous_value: float | None
    delta: float | None
    observation_date: date
    snapshot: IndicatorSnapshot | None = None
    state: StateRow | None = None


@dataclass(frozen=True, slots=True)
class StateObservation:
    active: bool
    observation_date: date
    state: StateRow
    value_context: dict[str, dict[str, float | None]]


class AlertObservationService:
    def __init__(
        self, session: AsyncSession, *, state_registry: StateRegistry | None = None
    ) -> None:
        self.session = session
        self.state_registry = state_registry or DEFAULT_REGISTRY
        self.alert_repository = AlertRepository(session)
        self.security_repository = SecurityRepository(session)
        self.dividend_service = DividendYieldService(self.security_repository)

    async def resolve_value(self, rule: AlertRule) -> ScalarObservation | None:
        indicator = str(rule.indicator_type or "").upper()
        if indicator == "DIVIDEND_YIELD":
            try:
                result = await self.dividend_service.calculate_for_security(rule.security_id)
            except ApiError as exc:
                if exc.code in {"PRICE_NOT_FOUND", "INVALID_PRICE", "INVALID_DIVIDEND_DATA"}:
                    return None
                raise
            return ScalarObservation(
                value=float(result.dividend_yield),
                previous_value=None,
                delta=None,
                observation_date=result.as_of.astimezone(ZoneInfo("Asia/Shanghai")).date(),
            )

        if not rule.parameter_key or not rule.field:
            return None
        stream = await published_stream_for_read(self.session, rule.security_id, rule.adjust_type)
        if not publication_is_current(stream):
            return None
        snapshot = await self.alert_repository.latest_snapshot(
            rule.security_id,
            indicator,
            adjustment=rule.adjust_type,
        )
        if snapshot is None:
            return None
        variant = select_snapshot_variant(snapshot, indicator, rule.parameters or {})
        if variant is None or variant.key != rule.parameter_key:
            return None
        value = _field(variant.values, rule.field)
        if value is None:
            return None
        previous = _field(variant.previous_values, rule.field)
        delta = _field(variant.delta, rule.field)
        return ScalarObservation(
            value=value,
            previous_value=previous,
            delta=delta,
            observation_date=snapshot.trade_date,
            snapshot=snapshot,
        )

    async def resolve_state(self, rule: AlertRule) -> StateObservation | None:
        if not rule.state_code or not rule.parameter_key:
            return None
        # Lightweight read adapters used by extensions need not own a session.
        if hasattr(getattr(self, "session", None), "execute"):
            stream = await published_stream_for_read(
                self.session, rule.security_id, rule.adjust_type
            )
            if not publication_is_current(stream):
                return None
        rows = await self.alert_repository.latest_states(
            rule.security_id,
            adjustment=rule.adjust_type,
            state_code=rule.state_code,
            parameter_key=rule.parameter_key,
        )
        if not rows:
            return None
        state = rows[0]
        if state.status == StateStatus.UNKNOWN.value or (state.metadata or {}).get("reason"):
            return None
        active = state.status == StateStatus.ACTIVE.value
        if self.state_registry.get(rule.state_code).metadata.get("mode") == "edge":
            active = active and bool((state.metadata or {}).get("transition", False))
        return StateObservation(
            active=active,
            observation_date=state.trade_date,
            state=state,
            value_context=_state_value_context(state.metadata),
        )


def _state_value_context(metadata: Any) -> dict[str, dict[str, float | None]]:
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("value_context")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, float | None]] = {}
    for raw_label, raw_values in raw.items():
        if (
            not isinstance(raw_label, str)
            or not raw_label.strip()
            or not isinstance(raw_values, dict)
        ):
            continue
        values: dict[str, float | None] = {}
        for key in ("current_value", "previous_value", "change"):
            value = raw_values.get(key)
            values[key] = (
                float(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None
            )
        result[raw_label.strip()] = values
    return result


def _field(values: Any, field: str) -> float | None:
    if not isinstance(values, dict):
        return None
    if (
        field in values
        and isinstance(values[field], (int, float))
        and not isinstance(values[field], bool)
        and math.isfinite(values[field])
    ):
        return float(values[field])
    folded = field.casefold()
    for key, value in values.items():
        if (
            isinstance(key, str)
            and key.casefold() == folded
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return float(value)
    return None


__all__ = ["AlertObservationService", "ScalarObservation", "StateObservation"]
