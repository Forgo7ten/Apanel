"""Application service for durable indicator state recognition."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators.parameters import (
    DEFAULT_MA_PERIODS,
    IndicatorVariant,
    select_snapshot_variant,
    snapshot_variants,
)
from app.models import (
    AnalysisStream,
)
from app.models import (
    IndicatorSnapshot as IndicatorSnapshotModel,
)
from app.repositories.indicator_state import IndicatorStateRepository
from app.repositories.state_event import StateEventRepository
from app.schemas.state_row import StateRow
from app.services.analysis_control import lock_analysis_reads, lock_analysis_stream
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
        end: date | None = None,
        commit: bool = True,
        persist: bool = True,
        max_rows: int | None = None,
        state_filter: str | None = None,
        parameter_filter: str | None = None,
        indicator_filter: str | None = None,
    ) -> list[StateRow]:
        adjustment = _validate_indicator_adjustment(adjustment)
        security = await self._get_security(symbol)
        if persist:
            stream = await lock_analysis_stream(self.session, security.id, adjustment)
            if stream.state_through is None:
                # First publication must establish an entire defensible
                # timeline, even if a caller requested only the latest day.
                start, latest_only = None, False
        else:
            stream = await self.session.get(AnalysisStream, (security.id, adjustment))
        calendar = TradingCalendarService(self.session)

        persist_from = start
        if latest_only:
            query = select(func.max(IndicatorSnapshotModel.trade_date)).where(
                IndicatorSnapshotModel.security_id == security.id,
                IndicatorSnapshotModel.adjust_type == adjustment,
            )
            if end is not None:
                query = query.where(IndicatorSnapshotModel.trade_date <= end)
            persist_from = (await self.session.execute(query)).scalar_one_or_none()
            if persist_from is None:
                raise ApiError("STATE_DATA_NOT_FOUND", "No indicator data is available.", 404)
        read_from = persist_from
        seed_date = None
        if persist_from is not None:
            # Public history bounds may start on a weekend/holiday. Resolve the
            # predecessor of the first actual observation, not of the requested
            # calendar date (which cannot itself be proven OPEN).
            first_query = select(func.min(IndicatorSnapshotModel.trade_date)).where(
                IndicatorSnapshotModel.security_id == security.id,
                IndicatorSnapshotModel.adjust_type == adjustment,
                IndicatorSnapshotModel.trade_date >= persist_from,
            )
            if end is not None:
                first_query = first_query.where(IndicatorSnapshotModel.trade_date <= end)
            first_date = (await self.session.execute(first_query)).scalar_one_or_none()
            if first_date is not None:
                seed_date = (await calendar.previous_open_dates((first_date,))).get(first_date)
            if seed_date is not None:
                read_from = seed_date
        snapshots = await self.repository.list_snapshots(
            security.id,
            adjustment=adjustment,
            start=read_from,
            end=end,
        )
        if not snapshots:
            raise ApiError("STATE_DATA_NOT_FOUND", "No indicator data is available.", 404)
        if persist:
            await self._persist_definitions()
        if seed_date is None:
            seed_states = []
        elif stream is not None and stream.state_through is not None:
            seed_states = await StateEventRepository(self.session).seed_at(
                security.id, adjustment, seed_date
            )
        else:
            seed_states = []
        seed_states = [
            row
            for row in seed_states
            if (state_filter is None or row.state_code == state_filter)
            and (parameter_filter is None or row.parameter_key == parameter_filter)
            and (indicator_filter is None or row.indicator_type == indicator_filter)
        ]
        existing_by_key = {
            (item.trade_date, item.state_code, item.parameter_key): item for item in seed_states
        }
        computed_status_by_key: dict[tuple[date, str, str], StateStatus] = {}
        bars = await self.repository.get_daily_bars(
            security.id, adjustment=adjustment, start=read_from, end=end
        )
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
        persisted: list[StateRow] = []
        known_targets = {(row.state_code, row.parameter_key): row for row in seed_states}

        for trade_date in dates:
            if trade_date not in dates_to_persist:
                continue
            date_offset = len(persisted)
            expected_previous_date = previous_open_by_date.get(trade_date)
            current_by_indicator = by_date[trade_date]
            previous_by_indicator = (
                by_date.get(expected_previous_date, {})
                if expected_previous_date is not None
                else {}
            )
            for definition in self.registry.definitions():
                if state_filter is not None and definition.code != state_filter:
                    continue
                if indicator_filter is not None and definition.indicator_type != indicator_filter:
                    continue
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
                    if parameter_filter is not None and state_key != parameter_filter:
                        continue
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

                    previous_key = (expected_previous_date, definition.code, state_key)
                    prior_status = computed_status_by_key.get(previous_key)
                    if prior_status is None:
                        prior_row = existing_by_key.get(previous_key)
                        prior_status = (
                            _state_status(prior_row.status) if prior_row is not None else None
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

                    if result.reason:
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
                    state = StateRow(
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
                            "transition": bool(
                                result.transition
                                and (
                                    definition.metadata.get("mode") == "edge"
                                    or prior_status is StateStatus.INACTIVE
                                )
                            ),
                            "reason": result.reason,
                            "values": dict(result.values),
                            "value_context": _state_value_context(
                                definition,
                                current,
                                previous,
                                current_price=close_by_date.get(trade_date),
                                previous_price=close_by_date.get(expected_previous_date),
                            ),
                            "definition": _json_value(result.metadata),
                        },
                    )
                    persisted.append(state)
            present = {(row.state_code, row.parameter_key) for row in persisted[date_offset:]}
            for target, old in tuple(known_targets.items()):
                if target in present:
                    continue
                unknown = StateRow(
                    security_id=security.id,
                    trade_date=trade_date,
                    state_code=old.state_code,
                    indicator_type=old.indicator_type,
                    status="UNKNOWN",
                    parameters=dict(old.parameters),
                    parameter_key=old.parameter_key,
                    adjust_type=adjustment,
                    metadata={
                        "name": old.metadata_json.get("name", old.state_code),
                        "level": old.metadata_json.get("level", "INFO"),
                        "active": False,
                        "transition": False,
                        "reason": "missing_current_indicator",
                        "values": {},
                        "definition": old.metadata_json.get("definition", {}),
                    },
                )
                persisted.append(unknown)
                computed_status_by_key[(trade_date, *target)] = StateStatus.UNKNOWN
            known_targets.update(
                {(row.state_code, row.parameter_key): row for row in persisted[date_offset:]}
            )
            if max_rows is not None and len(persisted) > max_rows:
                raise ApiError(
                    "HISTORY_RESULT_TOO_LARGE", "Narrow the history range or parameter filter.", 413
                )
        if not persist:
            return persisted
        assert stream is not None
        through = end or dates[-1]
        await StateEventRepository(self.session).publish(
            stream,
            persisted,
            start=persist_from,
            through=through,
        )
        # The direct StateService materialization entry point shares the same
        # initialization watermark as AnalysisService. Without this write
        # freshly computed history looks permanently uninitialized.
        stream.state_through = max(stream.state_through or through, through)
        if commit:
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
    ) -> StateRow:
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
        return StateRow(
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
        end: date | None = None,
        commit: bool = True,
    ) -> list[StateRow]:
        return await self.calculate(
            symbol, adjustment=adjustment, start=start, end=end, commit=commit, latest_only=False
        )

    async def materialize_latest(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
        end: date | None = None,
        commit: bool = True,
    ) -> list[StateRow]:
        return await self.calculate(
            symbol, adjustment=adjustment, end=end, commit=commit, latest_only=True
        )

    async def current(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
    ) -> list[StateRow]:
        adjustment = _validate_indicator_adjustment(adjustment)
        security = await self._get_security(symbol)
        await lock_analysis_reads(self.session, [security.id], adjustment)
        rows = await StateEventRepository(self.session).current_many(
            [security.id], adjustment=adjustment
        )
        if not rows[security.id]:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        return [row for row in rows[security.id] if row.status == "ACTIVE"]

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
    ) -> list[StateRow]:
        adjustment = _validate_history_adjustment(adjustment)
        security = await self._get_security(symbol)
        normalized_indicator = (
            str(indicator_type).strip().upper() if indicator_type is not None else None
        )
        await lock_analysis_reads(self.session, [security.id], adjustment)
        stream = await self.session.get(AnalysisStream, (security.id, adjustment))
        if stream is None or stream.state_through is None:
            raise ApiError("STATE_EVENTS_NOT_READY", "State event history is not initialized.", 409)
        if stream.published_revision >= 0 and stream.dirty_from is not None:
            raise ApiError("ANALYSIS_REBUILD_PENDING", "Historical analysis is being rebuilt.", 503)
        rows = (
            await StateEventRepository(self.session).current_many(
                [security.id],
                adjustment=adjustment,
                state_code=state_code,
                parameter_key=parameter_key,
            )
        )[security.id]
        latest_date = max(
            (
                row.trade_date
                for row in rows
                if normalized_indicator is None or row.indicator_type == normalized_indicator
            ),
            default=None,
        )
        if latest_date is None:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        resolved_start, resolved_end = resolve_history_window(
            start=start, end=end, latest_date=latest_date
        )
        persisted = await self.calculate(
            symbol,
            adjustment=adjustment,
            start=resolved_start,
            end=resolved_end,
            persist=False,
            commit=False,
            max_rows=MAX_STATE_HISTORY_ROWS,
            state_filter=state_code,
            parameter_filter=parameter_key,
            indicator_filter=normalized_indicator,
        )
        persisted = [
            row
            for row in persisted
            if (state_code is None or row.state_code == state_code)
            and (parameter_key is None or row.parameter_key == parameter_key)
            and (normalized_indicator is None or row.indicator_type == normalized_indicator)
        ]
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
        existing = {
            row.state_code: row
            for row in await self.repository.list_state_definitions(enabled_only=False)
        }
        updates = []
        for definition in self.registry.definitions():
            metadata = _json_value(definition.metadata)
            payload = dict(
                state_code=definition.code,
                indicator_type=definition.indicator_type,
                name=definition.name,
                description=str(metadata.get("comparison", "")),
                level=definition.level,
                metadata=metadata,
                enabled=True,
            )
            old = existing.get(definition.code)
            if old is None or any(getattr(old, key) != value for key, value in payload.items()):
                updates.append(payload)
        if updates:
            await self.repository.upsert_definitions(updates)


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
        emitted: set[tuple[int, int]] = set()
        for current_variant in current_variants:
            periods = current_variant.parameters.get("periods")
            if not isinstance(periods, list):
                one = current_variant.parameters.get("period")
                periods = [one] if one is not None else []
            normalized_periods = sorted({int(period) for period in periods})
            pairs: tuple[tuple[int, int], ...]
            if len(normalized_periods) == 2:
                pairs = ((normalized_periods[0], normalized_periods[1]),)
            elif set(DEFAULT_MA_PERIODS).issubset(normalized_periods):
                # Current MA variants may contain multiple periods. Preserve
                # the built-in MA5/10 state without recreating every possible
                # cross-product from unrelated numeric MA columns.
                pairs = ((DEFAULT_MA_PERIODS[0], DEFAULT_MA_PERIODS[1]),)
            else:
                pairs = ()
            for short, long in pairs:
                if (short, long) in emitted:
                    continue
                emitted.add((short, long))
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


def _state_value_context(
    definition: Any,
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    current_price: float | None,
    previous_price: float | None,
) -> dict[str, dict[str, float | None]]:
    raw_fields = definition.metadata.get("fields", ())
    fields = tuple(str(field).strip().lower() for field in raw_fields if str(field).strip())
    result: dict[str, dict[str, float | None]] = {}
    for field in fields:
        label = field
        if current.indicator == "MA" and field in {"short", "long"}:
            period_key = f"{field}_period"
            period = current.parameters.get(period_key)
            if period is None:
                continue
            label = f"MA{int(period)}"
            value_key = label.lower()
            current_value = current.values.get(value_key)
            previous_value = previous.values.get(value_key)
        elif field == "price":
            label = "price"
            current_value = current_price
            previous_value = previous_price
        else:
            current_value = current.values.get(field)
            previous_value = previous.values.get(field)
        if current_value is None and previous_value is None:
            continue
        change = (
            float(current_value) - float(previous_value)
            if current_value is not None and previous_value is not None
            else None
        )
        result[label] = {
            "current_value": None if current_value is None else float(current_value),
            "previous_value": None if previous_value is None else float(previous_value),
            "change": change,
        }
    return result


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
