"""Application service for durable indicator calculations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators import IndicatorError, create_default_registry
from app.indicators.types import Candle
from app.models import DailyBar
from app.models import IndicatorSnapshot as IndicatorSnapshotModel
from app.repositories.indicator_state import IndicatorStateRepository
from app.schemas.indicator_state import IndicatorHistoryData, IndicatorSnapshotData


@dataclass(frozen=True, slots=True)
class CalculatedIndicator:
    """A normalized calculation ready for the persistence boundary."""

    indicator_type: str
    parameters: dict[str, Any]
    values: dict[str, float]


DEFAULT_ADJUSTMENT: str | None = None
DEFAULT_MA_PERIODS = (5, 10)
DEFAULT_PROJECTED_MA_PERIOD = 5
DEFAULT_RSI_PERIOD = 14
DEFAULT_KDJ_PERIODS = {"period": 9, "k_period": 3, "d_period": 3}
DEFAULT_BOLL_PARAMETERS = {"period": 20, "multiplier": 2.0}
DEFAULT_MACD_PARAMETERS = {"fast_period": 12, "slow_period": 26, "signal_period": 9}


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
        adjustment: str | None = DEFAULT_ADJUSTMENT,
    ) -> list[IndicatorSnapshotModel]:
        _validate_adjustment(adjustment)
        security = await self._get_security(symbol)
        bars = await self.repository.get_daily_bars(
            security.id,
            adjustment=adjustment,
        )
        bars = _select_one_adjustment_per_day(bars, preferred=adjustment)
        if not bars:
            raise ApiError("INDICATOR_DATA_NOT_FOUND", "No daily bars are available.", 404)

        snapshots: list[IndicatorSnapshotModel] = []
        previous_values: dict[str, dict[str, float]] = {}
        for index, bar in enumerate(bars):
            calculated = self._calculate_for_bars(bars[: index + 1])
            for item in calculated:
                prior = previous_values.get(item.indicator_type)
                delta = _delta(item.values, prior)
                snapshot = await self.repository.upsert_snapshot(
                    security_id=security.id,
                    trade_date=bar.trade_date,
                    indicator_type=item.indicator_type,
                    parameters=item.parameters,
                    values=item.values,
                    previous_values=prior,
                    delta=delta,
                )
                snapshots.append(snapshot)
                previous_values[item.indicator_type] = item.values

        await self.session.commit()
        return snapshots

    async def latest(
        self,
        symbol: str,
        *,
        adjustment: str | None = DEFAULT_ADJUSTMENT,
    ) -> list[IndicatorSnapshotModel]:
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
        adjustment: str | None = DEFAULT_ADJUSTMENT,
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
        adjustment: str | None = DEFAULT_ADJUSTMENT,
    ) -> list[IndicatorSnapshotModel]:
        _validate_range(start, end)
        security = await self._get_security(symbol)
        persisted = await self.repository.list_snapshots(
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
        snapshots = await self.repository.list_snapshots(
            security.id,
            start=start,
            end=end,
        )
        if not snapshots:
            raise ApiError("INDICATOR_DATA_NOT_FOUND", "No indicator data is available.", 404)
        return snapshots

    async def history_data(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        adjustment: str | None = DEFAULT_ADJUSTMENT,
    ) -> IndicatorHistoryData:
        """Return the public history projection for one security."""

        snapshots = await self.history(
            symbol,
            start=start,
            end=end,
            adjustment=adjustment,
        )
        return IndicatorHistoryData(
            items=[serialize_indicator_snapshot(item) for item in snapshots]
        )

    async def _get_security(self, symbol: str):
        normalized = normalize_symbol(symbol)
        security = await self.repository.get_security(normalized)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        return security

    def _calculate_for_bars(self, bars: Iterable[DailyBar]) -> tuple[CalculatedIndicator, ...]:
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

        ma_values: dict[str, float] = {}
        for period in DEFAULT_MA_PERIODS:
            try:
                result = self.registry.calculate("ma", candles, period=period)
            except IndicatorError:
                continue
            ma_values[f"MA{period}"] = _float_values(result.to_dict())["value"]
        if ma_values:
            calculated.append(
                CalculatedIndicator(
                    indicator_type="MA",
                    parameters={"periods": list(DEFAULT_MA_PERIODS)},
                    values=ma_values,
                )
            )

        calculated.extend(
            self._single_calculation(
                candles,
                "projected_ma",
                indicator_type="PROJECTED_MA",
                parameters={"period": DEFAULT_PROJECTED_MA_PERIOD},
            )
        )
        calculated.extend(
            self._single_calculation(
                candles,
                "rsi",
                indicator_type="RSI",
                parameters={"period": DEFAULT_RSI_PERIOD},
            )
        )
        calculated.extend(
            self._single_calculation(
                candles,
                "kdj",
                indicator_type="KDJ",
                parameters=DEFAULT_KDJ_PERIODS,
            )
        )
        calculated.extend(
            self._single_calculation(
                candles,
                "boll",
                indicator_type="BOLL",
                parameters=DEFAULT_BOLL_PARAMETERS,
            )
        )
        calculated.extend(
            self._single_calculation(
                candles,
                "macd",
                indicator_type="MACD",
                parameters=DEFAULT_MACD_PARAMETERS,
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
        values: dict[str, Any] = _float_values(dict(snapshot.values))
        if snapshot.delta is None:
            values["delta"] = None
        elif snapshot.indicator_type in {"RSI", "PROJECTED_MA"} and "value" in snapshot.delta:
            values["delta"] = float(snapshot.delta["value"])
        else:
            values["delta"] = _float_values(dict(snapshot.delta))
        data[snapshot.indicator_type] = values
    return data


def serialize_indicator_snapshot(snapshot: IndicatorSnapshotModel) -> IndicatorSnapshotData:
    """Serialize one persisted snapshot for the history API."""

    return IndicatorSnapshotData(
        trade_date=snapshot.trade_date,
        indicator_type=snapshot.indicator_type,
        parameters=dict(snapshot.parameters),
        values=_float_values(dict(snapshot.values)),
        previous_values=(
            _float_values(dict(snapshot.previous_values))
            if snapshot.previous_values is not None
            else None
        ),
        delta=(
            _float_values(dict(snapshot.delta)) if snapshot.delta is not None else None
        ),
    )


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


def _validate_adjustment(adjustment: str | None) -> None:
    if adjustment is not None and adjustment not in {"qfq", "none"}:
        raise ApiError("INVALID_ADJUSTMENT", "Adjustment must be qfq or none.", 400)


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
    "IndicatorService",
    "normalize_symbol",
    "serialize_current_indicators",
    "serialize_indicator_snapshot",
]
