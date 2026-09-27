"""Application service for durable indicator state recognition."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from itertools import combinations
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators.parameters import IndicatorVariant, select_snapshot_variant, snapshot_variants
from app.models import (
    IndicatorSnapshot as IndicatorSnapshotModel,
)
from app.models import (
    IndicatorState,
)
from app.repositories.indicator_state import IndicatorStateRepository
from app.services.history_window import MAX_STATE_HISTORY_ROWS, resolve_history_window
from app.services.indicator_service import (
    DEFAULT_HISTORY_ADJUSTMENT,
    DEFAULT_INDICATOR_ADJUSTMENT,
    _validate_history_adjustment,
    _validate_indicator_adjustment,
    normalize_symbol,
)
from app.services.trading_calendar_service import TradingCalendarService
from app.states import DEFAULT_REGISTRY, IndicatorSnapshot, StateEngine, StateRegistry, StateStatus


class StateService:
    """Recognize persisted snapshots without recalculating indicators on reads."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: StateRegistry | None = None,
    ) -> None:
        self.session = session
        self.repository = IndicatorStateRepository(session)
        self.registry = registry or DEFAULT_REGISTRY

    async def calculate(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
        start: date | None = None,
        latest_only: bool = False,
    ) -> list[IndicatorState]:
        adjustment = _validate_indicator_adjustment(adjustment)
        security = await self._get_security(symbol)
        calendar = TradingCalendarService(self.session)

        persist_from = start
        read_from = start
        if start is not None:
            previous_for_start = (await calendar.previous_open_dates((start,))).get(start)
            if previous_for_start is not None:
                read_from = previous_for_start

        snapshots = await self.repository.list_snapshots(
            security.id,
            adjustment=adjustment,
            start=read_from,
        )
        if not snapshots:
            raise ApiError("STATE_DATA_NOT_FOUND", "No indicator data is available.", 404)

        await self._persist_definitions()
        existing_states = await self.repository.list_states(security.id, adjustment=adjustment)
        existing_by_key = {
            (item.trade_date, item.state_code, item.parameter_key): item for item in existing_states
        }
        computed_status_by_key: dict[tuple[date, str, str], StateStatus] = {}
        bars = await self.repository.get_daily_bars(security.id, adjustment=adjustment)
        close_by_date = {item.trade_date: float(item.close) for item in bars}

        by_date: dict[date, dict[str, IndicatorSnapshotModel]] = defaultdict(dict)
        for snapshot in snapshots:
            by_date[snapshot.trade_date][snapshot.indicator_type] = snapshot
        dates = sorted(by_date)
        previous_open_by_date = await calendar.previous_open_dates(dates)
        if latest_only and dates:
            dates_to_persist = {dates[-1]}
        elif persist_from is not None:
            dates_to_persist = {item for item in dates if item >= persist_from}
        else:
            dates_to_persist = set(dates)
        engine = StateEngine(registry=self.registry)
        persisted: list[IndicatorState] = []

        for trade_date in dates:
            expected_previous_date = previous_open_by_date.get(trade_date)
            current_by_indicator = by_date[trade_date]
            previous_by_indicator = (
                by_date.get(expected_previous_date, {})
                if expected_previous_date is not None
                else {}
            )
            for definition in self.registry.definitions():
                current_model = current_by_indicator.get(definition.indicator_type)
                if current_model is None:
                    continue
                previous_model = previous_by_indicator.get(definition.indicator_type)
                for state_parameters, current_variant, previous_variant in _state_variant_pairs(
                    definition.indicator_type,
                    current_model,
                    previous_model,
                ):
                    if current_variant is None:
                        continue
                    state_key = state_parameter_key(definition.code, state_parameters)
                    current = _to_domain_snapshot(current_model, current_variant)
                    if current is None:
                        continue

                    if expected_previous_date is None:
                        computed_status_by_key[(trade_date, definition.code, state_key)] = (
                            StateStatus.UNKNOWN
                        )
                        if trade_date in dates_to_persist:
                            persisted.append(
                                await self._persist_unknown_state(
                                    security_id=security.id,
                                    trade_date=trade_date,
                                    adjustment=adjustment,
                                    definition=definition,
                                    state_parameters=state_parameters,
                                    state_key=state_key,
                                    current=current,
                                    reason="trading_calendar_unavailable",
                                    expected_previous_date=None,
                                )
                            )
                        continue

                    previous = _to_domain_snapshot(previous_model, previous_variant)
                    if previous is None:
                        computed_status_by_key[(trade_date, definition.code, state_key)] = (
                            StateStatus.UNKNOWN
                        )
                        if trade_date in dates_to_persist:
                            persisted.append(
                                await self._persist_unknown_state(
                                    security_id=security.id,
                                    trade_date=trade_date,
                                    adjustment=adjustment,
                                    definition=definition,
                                    state_parameters=state_parameters,
                                    state_key=state_key,
                                    current=current,
                                    reason="missing_previous_trading_day",
                                    expected_previous_date=expected_previous_date,
                                )
                            )
                        continue

                    prior_row = existing_by_key.get(
                        (expected_previous_date, definition.code, state_key)
                    )
                    prior_status = (
                        _state_status(prior_row.status) if prior_row is not None else None
                    )
                    if prior_status is None:
                        prior_status = computed_status_by_key.get(
                            (expected_previous_date, definition.code, state_key)
                        )
                    previous_active = (
                        False
                        if prior_status is StateStatus.UNKNOWN
                        else prior_status is StateStatus.ACTIVE
                        if prior_status is not None
                        else None
                    )
                    result = engine.evaluate_state(
                        definition.code,
                        current,
                        previous,
                        current_price=close_by_date.get(trade_date),
                        previous_price=close_by_date.get(expected_previous_date),
                        stream_id=f"security:{security.id}:{adjustment}:{state_key}",
                        previous_active=previous_active,
                    )

                    if prior_status is StateStatus.UNKNOWN and (result.active or result.reason):
                        computed_status_by_key[(trade_date, definition.code, state_key)] = (
                            StateStatus.UNKNOWN
                        )
                        if trade_date in dates_to_persist:
                            persisted.append(
                                await self._persist_unknown_state(
                                    security_id=security.id,
                                    trade_date=trade_date,
                                    adjustment=adjustment,
                                    definition=definition,
                                    state_parameters=state_parameters,
                                    state_key=state_key,
                                    current=current,
                                    reason=result.reason or "previous_state_unknown",
                                    expected_previous_date=expected_previous_date,
                                )
                            )
                        continue

                    computed_status_by_key[(trade_date, definition.code, state_key)] = result.status
                    if trade_date not in dates_to_persist:
                        continue
                    state = await self.repository.upsert_state(
                        security_id=security.id,
                        trade_date=trade_date,
                        state_code=result.code,
                        indicator_type=result.indicator_type,
                        status=result.status.value,
                        parameters=state_parameters,
                        parameter_key=state_key,
                        adjust_type=adjustment,
                        metadata={
                            "name": result.name,
                            "level": result.level,
                            "active": result.active,
                            "transition": result.transition,
                            "reason": result.reason,
                            "values": dict(result.values),
                            "definition": _json_value(result.metadata),
                        },
                    )
                    persisted.append(state)
        await self.session.commit()
        return persisted

    async def _persist_unknown_state(
        self,
        *,
        security_id: int,
        trade_date: date,
        adjustment: str,
        definition: Any,
        state_parameters: Mapping[str, Any],
        state_key: str,
        current: IndicatorSnapshot,
        reason: str,
        expected_previous_date: date | None,
    ) -> IndicatorState:
        metadata: dict[str, Any] = {
            "name": definition.name,
            "level": definition.level,
            "active": False,
            "transition": False,
            "reason": reason,
            "values": dict(current.values),
            "definition": _json_value(definition.metadata),
        }
        if expected_previous_date is not None:
            metadata["expected_previous_trade_date"] = expected_previous_date.isoformat()
        return await self.repository.upsert_state(
            security_id=security_id,
            trade_date=trade_date,
            state_code=definition.code,
            indicator_type=definition.indicator_type,
            status=StateStatus.UNKNOWN.value,
            parameters=dict(state_parameters),
            parameter_key=state_key,
            adjust_type=adjustment,
            metadata=metadata,
        )

    async def rebuild_history(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
        start: date | None = None,
    ) -> list[IndicatorState]:
        return await self.calculate(symbol, adjustment=adjustment, start=start, latest_only=False)

    async def materialize_latest(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
    ) -> list[IndicatorState]:
        return await self.calculate(symbol, adjustment=adjustment, latest_only=True)

    async def current(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
    ) -> list[IndicatorState]:
        adjustment = _validate_indicator_adjustment(adjustment)
        security = await self._get_security(symbol)
        latest_date = await self.repository.latest_state_date(security.id, adjustment=adjustment)
        if latest_date is None:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        return await self.repository.list_states(
            security.id,
            start=latest_date,
            end=latest_date,
            active_only=True,
            adjustment=adjustment,
        )

    async def history(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: str | None = DEFAULT_HISTORY_ADJUSTMENT,
        state_code: str | None = None,
        parameter_key: str | None = None,
        indicator_type: str | None = None,
    ) -> list[IndicatorState]:
        adjustment = _validate_history_adjustment(adjustment)
        security = await self._get_security(symbol)
        normalized_indicator = (
            str(indicator_type).strip().upper() if indicator_type is not None else None
        )
        latest_date = await self.repository.latest_state_date(
            security.id,
            adjustment=adjustment,
            state_code=state_code,
            parameter_key=parameter_key,
            indicator_type=normalized_indicator,
        )
        if latest_date is None:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        resolved_start, resolved_end = resolve_history_window(
            start=start, end=end, latest_date=latest_date
        )
        persisted = await self.repository.list_states(
            security.id,
            start=resolved_start,
            end=resolved_end,
            adjustment=adjustment,
            state_code=state_code,
            parameter_key=parameter_key,
            indicator_type=normalized_indicator,
            limit=MAX_STATE_HISTORY_ROWS + 1,
        )
        if not persisted:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        if len(persisted) > MAX_STATE_HISTORY_ROWS:
            raise ApiError(
                "HISTORY_RESULT_TOO_LARGE",
                "State history result is too large; narrow the range or filters.",
                413,
            )
        return persisted

    async def _get_security(self, symbol: str):
        security = await self.repository.get_security(normalize_symbol(symbol))
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        return security

    async def _persist_definitions(self) -> None:
        for definition in self.registry.definitions():
            metadata = _json_value(definition.metadata)
            await self.repository.upsert_state_definition(
                state_code=definition.code,
                indicator_type=definition.indicator_type,
                name=definition.name,
                description=str(metadata.get("comparison", "")),
                level=definition.level,
                metadata=metadata,
                enabled=True,
            )


def _state_status(value: Any) -> StateStatus | None:
    try:
        return StateStatus(str(value))
    except (TypeError, ValueError):
        return None


def state_parameter_key(state_code: str, parameters: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"state_code": state_code, "parameters": _json_value(parameters)},
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"v1_{hashlib.sha256(encoded).hexdigest()}"


def _state_variant_pairs(
    indicator_type: str,
    current_model: IndicatorSnapshotModel,
    previous_model: IndicatorSnapshotModel | None,
):
    current_variants = snapshot_variants(current_model)
    previous_variants = snapshot_variants(previous_model) if previous_model is not None else ()
    if indicator_type == "MA":
        for current_variant in current_variants:
            periods = current_variant.parameters.get("periods")
            if not isinstance(periods, list):
                one = current_variant.parameters.get("period")
                periods = [one] if one is not None else []
            normalized_periods = sorted({int(period) for period in periods})
            for short, long in combinations(normalized_periods, 2):
                params = {"short_period": short, "long_period": long}
                current = _project_ma_pair(current_variant, short, long)
                previous = _find_ma_pair(previous_variants, short, long)
                yield params, current, previous
        return
    for current_variant in current_variants:
        previous_variant = next(
            (item for item in previous_variants if item.key == current_variant.key),
            None,
        )
        yield dict(current_variant.parameters), current_variant, previous_variant


def _project_ma_pair(variant: IndicatorVariant, short: int, long: int) -> IndicatorVariant:
    keys = {f"MA{short}", f"MA{long}"}
    values = {
        key: value
        for key, value in variant.values.items()
        if key.upper() in {item.upper() for item in keys}
    }
    previous = (
        {
            key: value
            for key, value in variant.previous_values.items()
            if key.upper() in {item.upper() for item in keys}
        }
        if variant.previous_values is not None
        else None
    )
    delta = (
        {
            key: value
            for key, value in variant.delta.items()
            if key.upper() in {item.upper() for item in keys}
        }
        if variant.delta is not None
        else None
    )
    return IndicatorVariant(
        "MA",
        f"pair:{short}:{long}",
        {"short_period": short, "long_period": long},
        values,
        previous,
        delta,
        variant.source_format,
    )


def _find_ma_pair(
    variants: Iterable[IndicatorVariant], short: int, long: int
) -> IndicatorVariant | None:
    for variant in variants:
        projected = _project_ma_pair(variant, short, long)
        if len(projected.values) == 2:
            return projected
    return None


def _to_domain_snapshot(
    model: IndicatorSnapshotModel | None,
    variant: IndicatorVariant | None = None,
) -> IndicatorSnapshot | None:
    if model is None:
        return None
    variant = variant or select_snapshot_variant(model, model.indicator_type)
    if variant is None:
        return None
    return IndicatorSnapshot(
        indicator=model.indicator_type,
        values=variant.values,
        previous_values=variant.previous_values,
        # The state domain contract accepts scalar numeric parameters.  MA
        # stores its two periods as a JSON list for the persistence/API
        # contract, so retain only scalar entries at this boundary.
        parameters={
            key: value
            for key, value in variant.parameters.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        },
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def _select_one_adjustment_per_day(items: Iterable[Any], *, preferred: str | None):
    by_date: dict[date, list[Any]] = {}
    for item in items:
        by_date.setdefault(item.trade_date, []).append(item)
    selected: list[Any] = []
    for trade_date in sorted(by_date):
        candidates = by_date[trade_date]
        if preferred is not None:
            preferred_candidates = [item for item in candidates if item.adjust_type == preferred]
            if preferred_candidates:
                selected.append(preferred_candidates[0])
                continue
        for adjustment in ("qfq", "none"):
            preferred_candidates = [item for item in candidates if item.adjust_type == adjustment]
            if preferred_candidates:
                selected.append(preferred_candidates[0])
                break
        else:
            selected.append(candidates[0])
    return selected


__all__ = ["StateService"]
