"""Resolve one precise observation target for an alert rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators.parameters import select_snapshot_variant
from app.models import AlertRule, IndicatorSnapshot, IndicatorState
from app.repositories.alert import AlertRepository
from app.repositories.security import SecurityRepository
from app.services.dividend_service import DividendYieldService
from app.states import StateStatus


@dataclass(frozen=True, slots=True)
class ScalarObservation:
    value: float
    previous_value: float | None
    delta: float | None
    observation_date: date
    snapshot: IndicatorSnapshot | None = None
    state: IndicatorState | None = None


@dataclass(frozen=True, slots=True)
class StateObservation:
    active: bool
    observation_date: date
    state: IndicatorState


class AlertObservationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
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
        rows = await self.alert_repository.latest_states(
            rule.security_id,
            adjustment=rule.adjust_type,
            state_code=rule.state_code,
            parameter_key=rule.parameter_key,
        )
        if not rows:
            legacy = await self.alert_repository.latest_states(
                rule.security_id,
                state_code=rule.state_code,
            )
            rows = (
                legacy
                if len(legacy) == 1
                else [item for item in legacy if item.parameter_key == "default"]
            )
        if not rows:
            return None
        state = rows[0]
        if state.status == StateStatus.UNKNOWN.value:
            return None
        return StateObservation(
            active=state.status == StateStatus.ACTIVE.value,
            observation_date=state.trade_date,
            state=state,
        )


def _field(values: Any, field: str) -> float | None:
    if not isinstance(values, dict):
        return None
    if (
        field in values
        and isinstance(values[field], (int, float))
        and not isinstance(values[field], bool)
    ):
        return float(values[field])
    folded = field.casefold()
    for key, value in values.items():
        if (
            isinstance(key, str)
            and key.casefold() == folded
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            return float(value)
    return None


__all__ = ["AlertObservationService", "ScalarObservation", "StateObservation"]
