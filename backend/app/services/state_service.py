"""Application service for durable indicator state recognition."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import (
    IndicatorSnapshot as IndicatorSnapshotModel,
)
from app.models import (
    IndicatorState,
)
from app.repositories.indicator_state import IndicatorStateRepository
from app.services.indicator_service import IndicatorService, _validate_range, normalize_symbol
from app.states import DEFAULT_REGISTRY, IndicatorSnapshot, StateEngine, StateRegistry, StateStatus


class StateService:
    """Recognize and persist state rows using database-backed prior status."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: StateRegistry | None = None,
    ) -> None:
        self.session = session
        self.repository = IndicatorStateRepository(session)
        self.indicator_service = IndicatorService(session)
        self.registry = registry or DEFAULT_REGISTRY

    async def calculate(
        self,
        symbol: str,
        *,
        adjustment: str | None = None,
    ) -> list[IndicatorState]:
        await self.indicator_service.calculate(symbol, adjustment=adjustment)
        security = await self._get_security(symbol)
        snapshots = await self.repository.list_snapshots(security.id)
        if not snapshots:
            raise ApiError("STATE_DATA_NOT_FOUND", "No indicator data is available.", 404)

        await self._persist_definitions()
        existing_states = await self.repository.list_states(security.id)
        existing_by_key = {
            (item.trade_date, item.state_code): item for item in existing_states
        }
        computed_by_key: dict[tuple[date, str], bool] = {}
        bars = await self.repository.get_daily_bars(security.id, adjustment=adjustment)
        close_by_date = {
            item.trade_date: float(item.close)
            for item in _select_one_adjustment_per_day(bars, preferred=adjustment)
        }

        by_date: dict[date, dict[str, IndicatorSnapshotModel]] = defaultdict(dict)
        for snapshot in snapshots:
            by_date[snapshot.trade_date][snapshot.indicator_type] = snapshot
        dates = sorted(by_date)
        engine = StateEngine(registry=self.registry)
        persisted: list[IndicatorState] = []

        for index, trade_date in enumerate(dates):
            previous_date = dates[index - 1] if index else None
            current_by_indicator = by_date[trade_date]
            previous_by_indicator = by_date.get(previous_date, {}) if previous_date else {}
            for definition in self.registry.definitions():
                current_model = current_by_indicator.get(definition.indicator_type)
                if current_model is None:
                    continue
                previous_model = previous_by_indicator.get(definition.indicator_type)
                current = _to_domain_snapshot(current_model)
                previous = _to_domain_snapshot(previous_model) if previous_model else None
                prior_row = (
                    existing_by_key.get((previous_date, definition.code))
                    if previous_date is not None
                    else None
                )
                prior_active = (
                    prior_row.status == StateStatus.ACTIVE.value
                    if prior_row is not None
                    else computed_by_key.get((previous_date, definition.code), False)
                )
                current_close = close_by_date.get(trade_date)
                previous_close = close_by_date.get(previous_date) if previous_date else None
                result = engine.evaluate_state(
                    definition.code,
                    current,
                    previous,
                    current_price=current_close,
                    previous_price=previous_close,
                    stream_id=f"security:{security.id}",
                    previous_active=prior_active,
                )
                state = await self.repository.upsert_state(
                    security_id=security.id,
                    trade_date=trade_date,
                    state_code=result.code,
                    indicator_type=result.indicator_type,
                    status=result.status.value,
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
                computed_by_key[(trade_date, definition.code)] = result.active

        await self.session.commit()
        return persisted

    async def current(
        self,
        symbol: str,
        *,
        adjustment: str | None = None,
    ) -> list[IndicatorState]:
        security = await self._get_security(symbol)
        persisted_date = await self.repository.latest_state_date(security.id)
        persisted = (
            await self.repository.list_states(
                security.id,
                start=persisted_date,
                end=persisted_date,
                active_only=True,
            )
            if persisted_date is not None
            else []
        )
        try:
            await self.calculate(symbol, adjustment=adjustment)
        except ApiError as error:
            if error.code != "INDICATOR_DATA_NOT_FOUND" or persisted_date is None:
                raise
            return persisted
        latest_date = await self.repository.latest_state_date(security.id)
        if latest_date is None:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        return await self.repository.list_states(
            security.id,
            start=latest_date,
            end=latest_date,
            active_only=True,
        )

    async def history(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: str | None = None,
    ) -> list[IndicatorState]:
        _validate_range(start, end)
        security = await self._get_security(symbol)
        persisted = await self.repository.list_states(
            security.id,
            start=start,
            end=end,
        )
        try:
            await self.calculate(symbol, adjustment=adjustment)
        except ApiError as error:
            if error.code != "INDICATOR_DATA_NOT_FOUND" or not persisted:
                raise
            return persisted
        states = await self.repository.list_states(
            security.id,
            start=start,
            end=end,
        )
        if not states:
            raise ApiError("STATE_DATA_NOT_FOUND", "No state data is available.", 404)
        return states

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


def _to_domain_snapshot(model: IndicatorSnapshotModel | None) -> IndicatorSnapshot | None:
    if model is None:
        return None
    return IndicatorSnapshot(
        indicator=model.indicator_type,
        values=model.values,
        previous_values=model.previous_values,
        # The state domain contract accepts scalar numeric parameters.  MA
        # stores its two periods as a JSON list for the persistence/API
        # contract, so retain only scalar entries at this boundary.
        parameters={
            key: value
            for key, value in model.parameters.items()
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
