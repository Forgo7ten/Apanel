"""Application service for durable indicator calculations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators import IndicatorError, create_default_registry
from app.indicators import parameters as _parameter_contract
from app.indicators.parameters import (
    IndicatorRequest,
    canonicalize_parameters,
    merge_indicator_requests,
    parameter_key,
    select_snapshot_variant,
    snapshot_variants,
)
from app.indicators.types import Candle
from app.models import DailyBar
from app.models import IndicatorSnapshot as IndicatorSnapshotModel
from app.repositories.indicator_state import IndicatorStateRepository
from app.schemas.indicator_state import IndicatorHistoryData, IndicatorSnapshotData

DEFAULT_BOLL_PARAMETERS = _parameter_contract.DEFAULT_BOLL_PARAMETERS
DEFAULT_KDJ_PERIODS = _parameter_contract.DEFAULT_KDJ_PERIODS
DEFAULT_MACD_PARAMETERS = _parameter_contract.DEFAULT_MACD_PARAMETERS
DEFAULT_MA_PERIODS = _parameter_contract.DEFAULT_MA_PERIODS
DEFAULT_PROJECTED_MA_PERIOD = _parameter_contract.DEFAULT_PROJECTED_MA_PERIOD
DEFAULT_RSI_PERIOD = _parameter_contract.DEFAULT_RSI_PERIOD
DEFAULT_INDICATOR_ADJUSTMENT = "qfq"
DEFAULT_HISTORY_ADJUSTMENT = DEFAULT_INDICATOR_ADJUSTMENT


@dataclass(frozen=True, slots=True)
class CalculatedIndicator:
    """A normalized calculation ready for the persistence boundary."""

    indicator_type: str
    parameters: dict[str, Any]
    values: dict[str, float]

    @property
    def key(self) -> str:
        return parameter_key(self.indicator_type, self.parameters)


# Backward-compatible name for callers that imported the old default; indicator
# persistence no longer has an unadjusted default.
DEFAULT_ADJUSTMENT = DEFAULT_INDICATOR_ADJUSTMENT


class IndicatorService:
    """Calculate and persist all built-in v1 indicators.

    The service deliberately calculates from completed daily bars only.  It
    rebuilds each requested day from the ordered bar series, which makes a
    retry after a process restart deterministic and gives every row its
    previous values and delta without any process-local cache.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = IndicatorStateRepository(session)
        self.registry = create_default_registry()

    async def calculate(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
        requests: Iterable[
            IndicatorRequest | tuple[str, Mapping[str, Any]] | Mapping[str, Any]
        ]
        | None = None,
    ) -> list[IndicatorSnapshotModel]:
        _validate_indicator_adjustment(adjustment)
        security = await self._get_security(symbol)
        persisted = await self.repository.list_snapshots(security.id)
        effective_requests = merge_indicator_requests(requests)
        if requests is None:
            # State/alert/API retries must not erase user-requested variants
            # already materialized by the EOD pipeline.  Defaults remain the
            # minimum set when no persisted custom request exists.
            existing_requests = [
                IndicatorRequest(variant.indicator_type, variant.parameters)
                for snapshot in persisted
                for variant in snapshot_variants(snapshot)
            ]
            effective_requests = merge_indicator_requests(existing_requests)
        bars = await self.repository.get_daily_bars(
            security.id,
            adjustment=adjustment,
        )
        bars = _select_one_adjustment_per_day(bars, preferred=adjustment)
        if not bars:
            raise ApiError("INDICATOR_DATA_NOT_FOUND", "No daily bars are available.", 404)

        snapshots: list[IndicatorSnapshotModel] = []
        previous_values: dict[tuple[str, str], dict[str, float]] = {}
        for index, bar in enumerate(bars):
            calculated = self._calculate_for_bars(bars[: index + 1], effective_requests)
            by_type: dict[str, list[CalculatedIndicator]] = defaultdict(list)
            for item in calculated:
                by_type[item.indicator_type].append(item)
            for indicator_type, items in by_type.items():
                parameters = {
                    "_format": 2,
                    "variants": {item.key: dict(item.parameters) for item in items},
                }
                values = {item.key: dict(item.values) for item in items}
                prior_by_variant: dict[str, dict[str, float] | None] = {}
                delta_by_variant: dict[str, dict[str, float] | None] = {}
                for item in items:
                    prior = previous_values.get((indicator_type, item.key))
                    prior_by_variant[item.key] = prior
                    delta_by_variant[item.key] = _delta(item.values, prior)
                snapshot = await self.repository.upsert_snapshot(
                    security_id=security.id,
                    trade_date=bar.trade_date,
                    indicator_type=indicator_type,
                    parameters=parameters,
                    values=values,
                    previous_values=prior_by_variant,
                    delta=delta_by_variant,
                )
                snapshots.append(snapshot)
                for item in items:
                    previous_values[(indicator_type, item.key)] = item.values

        await self.session.commit()
        return snapshots

    async def latest(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
    ) -> list[IndicatorSnapshotModel]:
        _validate_indicator_adjustment(adjustment)
        security = await self._get_security(symbol)
        persisted = await self.repository.latest_snapshots(security.id)
        try:
            await self.calculate(symbol, adjustment=adjustment)
        except ApiError as error:
            if error.code != "INDICATOR_DATA_NOT_FOUND" or not persisted:
                raise
            return persisted
        return await self.repository.latest_snapshots(security.id)

    async def latest_data(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_INDICATOR_ADJUSTMENT,
    ) -> dict[str, Any]:
        """Return the public latest-indicator projection.

        API controllers should not know how persistence rows map to the
        public indicator contract.  Keeping this projection here also gives
        ``delta`` one stable meaning: ``None`` means that no prior value
        exists, while a present delta is normalized to numbers.
        """

        return serialize_current_indicators(
            await self.latest(symbol, adjustment=adjustment)
        )

    async def history(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: str | None = DEFAULT_HISTORY_ADJUSTMENT,
    ) -> list[IndicatorSnapshotModel]:
        _validate_range(start, end)
        _validate_history_adjustment(adjustment)
        security = await self._get_security(symbol)
        persisted = await self.repository.list_snapshots(
            security.id,
            start=start,
            end=end,
        )
        if not persisted:
            raise ApiError("INDICATOR_DATA_NOT_FOUND", "No indicator data is available.", 404)
        # History is an observation endpoint.  It deliberately does not call
        # calculate(): a browser GET must not create rows, commit a session,
        # or silently choose a different adjustment series.
        return persisted

    async def history_data(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: str | None = DEFAULT_HISTORY_ADJUSTMENT,
        parameter_key: str | None = None,
    ) -> IndicatorHistoryData:
        """Return the public history projection for one security."""

        snapshots = await self.history(
            symbol,
            start=start,
            end=end,
            adjustment=adjustment,
        )
        return IndicatorHistoryData(
            items=[
                serialize_indicator_snapshot(snapshot, variant)
                for snapshot in snapshots
                for variant in history_snapshot_variants(snapshot)
                if parameter_key is None or variant.key == parameter_key
            ]
        )

    async def _get_security(self, symbol: str):
        normalized = normalize_symbol(symbol)
        security = await self.repository.get_security(normalized)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        return security

    def _calculate_for_bars(
        self,
        bars: Iterable[DailyBar],
        requests: Iterable[IndicatorRequest],
    ) -> tuple[CalculatedIndicator, ...]:
        bars = tuple(bars)
        candles = tuple(
            Candle(
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                timestamp=bar.trade_date,
            )
            for bar in bars
        )
        calculated: list[CalculatedIndicator] = []

        for request in requests:
            indicator_type = request.indicator_type
            parameters = canonicalize_parameters(
                indicator_type,
                request.parameters,
                fill_defaults=True,
            )
            if indicator_type == "MA":
                ma_values: dict[str, float] = {}
                for period in parameters.get("periods", [parameters.get("period")]):
                    if period is None:
                        continue
                    try:
                        result = self.registry.calculate("ma", candles, period=period)
                    except IndicatorError:
                        continue
                    ma_values[f"MA{period}"] = _float_values(result.to_dict())["value"]
                if ma_values:
                    calculated.append(
                        CalculatedIndicator(
                            indicator_type="MA",
                            parameters=dict(parameters),
                            values=ma_values,
                        )
                    )
                continue

            registry_name = {
                "PROJECTED_MA": "projected_ma",
                "RSI": "rsi",
                "KDJ": "kdj",
                "BOLL": "boll",
                "MACD": "macd",
            }.get(indicator_type, indicator_type.lower())
            calculated.extend(
                self._single_calculation(
                    candles,
                    registry_name,
                    indicator_type=indicator_type,
                    parameters=parameters,
                )
            )
        return tuple(calculated)

    def _single_calculation(
        self,
        candles: tuple[Candle, ...],
        registry_name: str,
        *,
        indicator_type: str,
        parameters: dict[str, Any],
    ) -> tuple[CalculatedIndicator, ...]:
        try:
            result = self.registry.calculate(registry_name, candles, **parameters)
        except IndicatorError:
            return ()
        return (
            CalculatedIndicator(
                indicator_type=indicator_type,
                parameters=dict(parameters),
                values=_float_values(result.to_dict()),
            ),
        )


def normalize_symbol(value: str) -> str:
    """Normalize the public six-digit security path to the DB key."""

    candidate = value.strip().upper()
    if "." in candidate:
        candidate = candidate.rsplit(".", 1)[-1]
    elif ":" in candidate:
        candidate = candidate.rsplit(":", 1)[-1]
    if len(candidate) != 6 or not candidate.isdigit():
        raise ApiError("INVALID_SECURITY_SYMBOL", "Security symbol is invalid.", 400)
    return candidate


def _float_values(values: dict[str, Any]) -> dict[str, float]:
    return {str(key): float(value) for key, value in values.items()}


def serialize_current_indicators(
    snapshots: Iterable[IndicatorSnapshotModel],
) -> dict[str, Any]:
    """Serialize latest snapshots using the public grouped-indicator shape."""

    data: dict[str, Any] = {}
    for snapshot in snapshots:
        variant = _default_variant(snapshot)
        if variant is None:
            continue
        values: dict[str, Any] = _float_values(dict(variant.values))
        if variant.delta is None:
            values["delta"] = None
        elif snapshot.indicator_type in {"RSI", "PROJECTED_MA"} and "value" in variant.delta:
            values["delta"] = float(variant.delta["value"])
        else:
            values["delta"] = _float_values(dict(variant.delta))
        data[snapshot.indicator_type] = values
    return data


def serialize_indicator_snapshot(
    snapshot: IndicatorSnapshotModel,
    variant=None,
) -> IndicatorSnapshotData:
    """Serialize one persisted snapshot for the history API."""

    variant = variant or _default_variant(snapshot)
    if variant is None:
        variant = snapshot_variants(snapshot)[0]

    return IndicatorSnapshotData(
        trade_date=snapshot.trade_date,
        indicator_type=snapshot.indicator_type,
        parameter_key=variant.key,
        parameters=dict(variant.parameters),
        values=_float_values(dict(variant.values)),
        previous_values=(
            _float_values(dict(variant.previous_values))
            if variant.previous_values is not None
            else None
        ),
        delta=(
            _float_values(dict(variant.delta)) if variant.delta is not None else None
        ),
    )


def history_snapshot_variants(snapshot: IndicatorSnapshotModel):
    """Expand persisted variants into stable history series.

    Most v2 variants map one-to-one to a series.  MA is intentionally stored
    as one efficient bundle, so project its individual periods before exposing
    history; otherwise an MA5 column could only distinguish itself by
    inspecting the values map and would be easy to mix with MA20.
    """

    variants = snapshot_variants(snapshot)
    for variant in variants:
        if variant.indicator_type == "MA":
            periods = variant.parameters.get("periods")
            if isinstance(periods, list) and len(periods) > 1:
                for period in periods:
                    projected = select_snapshot_variant(
                        snapshot,
                        "MA",
                        {"period": period},
                    )
                    if projected is not None:
                        yield projected
                continue
        yield variant


def _default_variant(snapshot: IndicatorSnapshotModel):
    return select_snapshot_variant(snapshot, snapshot.indicator_type, None)


def _delta(
    values: dict[str, float], previous: dict[str, float] | None
) -> dict[str, float] | None:
    if previous is None:
        return None
    result = {
        key: value - float(previous[key])
        for key, value in values.items()
        if key in previous
    }
    return result or None


def _validate_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and start > end:
        raise ApiError("INVALID_DATE_RANGE", "Start date must not be after end date.", 400)


def _validate_indicator_adjustment(adjustment: str | None) -> None:
    if adjustment != DEFAULT_INDICATOR_ADJUSTMENT:
        raise ApiError(
            "UNSUPPORTED_INDICATOR_ADJUSTMENT",
            "Indicator and state persistence only supports qfq adjustment.",
            400,
        )


def _validate_history_adjustment(adjustment: str | None) -> None:
    """History exposes only the PRD's single qfq observation sequence."""

    if adjustment != DEFAULT_HISTORY_ADJUSTMENT:
        raise ApiError(
            "UNSUPPORTED_HISTORY_ADJUSTMENT",
            "Indicator history only supports the default qfq adjustment series.",
            400,
        )


def _select_one_adjustment_per_day(
    bars: Iterable[DailyBar], *, preferred: str | None
) -> list[DailyBar]:
    """Choose one coherent adjustment series when the shared table has both."""

    by_date: dict[date, list[DailyBar]] = {}
    for bar in bars:
        by_date.setdefault(bar.trade_date, []).append(bar)
    selected: list[DailyBar] = []
    for trade_date in sorted(by_date):
        candidates = by_date[trade_date]
        if preferred is not None:
            preferred_candidates = [bar for bar in candidates if bar.adjust_type == preferred]
            if preferred_candidates:
                selected.append(preferred_candidates[0])
                continue
        for adjustment in ("qfq", "none"):
            preferred_candidates = [bar for bar in candidates if bar.adjust_type == adjustment]
            if preferred_candidates:
                selected.append(preferred_candidates[0])
                break
        else:
            selected.append(candidates[0])
    return selected


__all__ = [
    "DEFAULT_ADJUSTMENT",
    "DEFAULT_INDICATOR_ADJUSTMENT",
    "DEFAULT_HISTORY_ADJUSTMENT",
    "IndicatorService",
    "normalize_symbol",
    "serialize_current_indicators",
    "serialize_indicator_snapshot",
]
