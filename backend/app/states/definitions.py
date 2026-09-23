"""Built-in state definitions and their pure evaluators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import InvalidStateDefinitionError, MissingStateValues
from .types import IndicatorSnapshot, PriceSnapshot, _freeze


@dataclass(frozen=True, slots=True)
class StateSignal:
    """Evaluator output before edge-trigger history is applied by the engine."""

    active: bool
    edge: bool = False


class StateEvaluator(Protocol):
    def __call__(
        self,
        current: IndicatorSnapshot,
        previous: IndicatorSnapshot,
        *,
        price: PriceSnapshot | None,
        previous_price: float | None,
        tolerance: float,
    ) -> StateSignal | bool: ...


@dataclass(frozen=True, slots=True)
class StateDefinition:
    """Fixed state identity and metadata plus a replaceable evaluator."""

    code: str
    name: str
    level: str
    indicator_type: str
    metadata: Mapping[str, Any]
    evaluator: StateEvaluator

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise InvalidStateDefinitionError("state code must be a non-empty string")
        if self.code.strip().upper() != self.code:
            raise InvalidStateDefinitionError("state code must be uppercase")
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidStateDefinitionError("state name must be a non-empty string")
        if not isinstance(self.level, str) or not self.level.strip():
            raise InvalidStateDefinitionError("state level must be a non-empty string")
        if not isinstance(self.indicator_type, str) or not self.indicator_type.strip():
            raise InvalidStateDefinitionError("indicator_type must be a non-empty string")
        if not isinstance(self.metadata, Mapping):
            raise InvalidStateDefinitionError("state metadata must be a mapping")
        frozen_metadata = _freeze(self.metadata)
        object.__setattr__(self, "metadata", frozen_metadata)
        object.__setattr__(self, "indicator_type", self.indicator_type.strip().upper())
        mode = frozen_metadata.get("mode")
        if mode not in {"edge", "continuous"}:
            raise InvalidStateDefinitionError("state metadata.mode must be edge or continuous")
        if not callable(self.evaluator):
            raise InvalidStateDefinitionError("state evaluator must be callable")


def _value(snapshot: IndicatorSnapshot, *names: str) -> float:
    for name in names:
        try:
            return snapshot.values[name.lower()]
        except KeyError:
            continue
    raise MissingStateValues()


def _values(snapshot: IndicatorSnapshot, names: tuple[str, ...]) -> tuple[float, ...]:
    return tuple(_value(snapshot, name) for name in names)


def _comparison(left: float, right: float, tolerance: float) -> int:
    if abs(left - right) <= tolerance:
        return 0
    return 1 if left > right else -1


def _direction(current: float, previous: float, tolerance: float) -> int:
    return _comparison(current, previous, tolerance)


def _require_pair(
    current: IndicatorSnapshot, previous: IndicatorSnapshot
) -> tuple[float, float, float, float]:
    current_short, current_long = _ma_pair(current)
    previous_short, previous_long = _ma_pair(previous)
    return current_short, current_long, previous_short, previous_long


def _ma_pair(snapshot: IndicatorSnapshot) -> tuple[float, float]:
    values = snapshot.values
    for short_names, long_names in (
        (("short", "fast", "ma_short", "fast_ma"), ("long", "slow", "ma_long", "slow_ma")),
        (("ma5",), ("ma10",)),
    ):
        try:
            return _value(snapshot, *short_names), _value(snapshot, *long_names)
        except MissingStateValues:
            continue

    # Provider payloads often use ma{period} keys.  Pick the two smallest
    # numeric periods, making the short/long ordering deterministic.
    candidates: list[tuple[int, float]] = []
    for key, value in values.items():
        digits = "".join(character for character in key if character.isdigit())
        if digits and key.startswith("ma"):
            candidates.append((int(digits), value))
    if len(candidates) >= 2:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1], candidates[1][1]
    raise MissingStateValues()


def _ma_cross_up(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
) -> StateSignal:
    current_short, current_long, previous_short, previous_long = _require_pair(current, previous)
    current_relation = _comparison(current_short, current_long, tolerance)
    previous_relation = _comparison(previous_short, previous_long, tolerance)
    return StateSignal(current_relation > 0, current_relation > 0 and previous_relation <= 0)


def _ma_cross_down(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
) -> StateSignal:
    current_short, current_long, previous_short, previous_long = _require_pair(current, previous)
    current_relation = _comparison(current_short, current_long, tolerance)
    previous_relation = _comparison(previous_short, previous_long, tolerance)
    return StateSignal(current_relation < 0, current_relation < 0 and previous_relation >= 0)


def _ma_gap(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    narrowing: bool,
) -> StateSignal:
    current_short, current_long, previous_short, previous_long = _require_pair(current, previous)
    current_gap = abs(current_short - current_long)
    previous_gap = abs(previous_short - previous_long)
    relation = _comparison(current_gap, previous_gap, tolerance)
    active = relation < 0 if narrowing else relation > 0
    return StateSignal(active, active)


def _kdj_cross(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    up: bool,
) -> StateSignal:
    current_k, current_d = _values(current, ("k", "d"))
    previous_k, previous_d = _values(previous, ("k", "d"))
    current_relation = _comparison(current_k, current_d, tolerance)
    previous_relation = _comparison(previous_k, previous_d, tolerance)
    if up:
        return StateSignal(current_relation > 0, current_relation > 0 and previous_relation <= 0)
    return StateSignal(current_relation < 0, current_relation < 0 and previous_relation >= 0)


def _all_direction(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    fields: tuple[str, ...],
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    rising: bool,
) -> StateSignal:
    current_values = _values(current, fields)
    previous_values = _values(previous, fields)
    comparisons = tuple(
        _direction(current_value, previous_value, tolerance)
        for current_value, previous_value in zip(current_values, previous_values, strict=True)
    )
    active = (
        all(comparison > 0 for comparison in comparisons)
        if rising
        else all(comparison < 0 for comparison in comparisons)
    )
    return StateSignal(active, active)


def _boll_width(
    snapshot: IndicatorSnapshot,
    *,
    tolerance: float,
) -> float:
    try:
        return _value(snapshot, "width", "band_width", "bandwidth")
    except MissingStateValues:
        upper, middle, lower = _values(snapshot, ("upper", "middle", "lower"))
        if abs(middle) <= tolerance:
            return 0.0
        return (upper - lower) / middle


def _boll_width_state(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    narrowing: bool,
) -> StateSignal:
    current_width = _boll_width(current, tolerance=tolerance)
    previous_width = _boll_width(previous, tolerance=tolerance)
    relation = _comparison(current_width, previous_width, tolerance)
    active = relation < 0 if narrowing else relation > 0
    return StateSignal(active, active)


def _boll_all_direction(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    rising: bool,
) -> StateSignal:
    return _all_direction(
        current,
        previous,
        fields=("upper", "middle", "lower"),
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        rising=rising,
    )


def _boll_break(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    upper: bool,
) -> StateSignal:
    if price is None:
        raise MissingStateValues("missing_price")
    prior_price = price.previous if previous_price is None else previous_price
    if prior_price is None:
        raise MissingStateValues("missing_price")
    current_band = _value(current, "upper" if upper else "lower")
    previous_band = _value(previous, "upper" if upper else "lower")
    if upper:
        active = current_price_above = _comparison(price.current, current_band, tolerance) > 0
        edge = current_price_above and _comparison(prior_price, previous_band, tolerance) <= 0
    else:
        active = current_price_below = _comparison(price.current, current_band, tolerance) < 0
        edge = current_price_below and _comparison(prior_price, previous_band, tolerance) >= 0
    return StateSignal(active, edge)


def _macd_cross(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    up: bool,
) -> StateSignal:
    current_diff, current_dea = _values(current, ("diff", "dea"))
    previous_diff, previous_dea = _values(previous, ("diff", "dea"))
    current_relation = _comparison(current_diff, current_dea, tolerance)
    previous_relation = _comparison(previous_diff, previous_dea, tolerance)
    if up:
        return StateSignal(current_relation > 0, current_relation > 0 and previous_relation <= 0)
    return StateSignal(current_relation < 0, current_relation < 0 and previous_relation >= 0)


def _macd_red_bar(
    current: IndicatorSnapshot,
    previous: IndicatorSnapshot,
    *,
    price: PriceSnapshot | None,
    previous_price: float | None,
    tolerance: float,
    growing: bool,
) -> StateSignal:
    current_bar = _value(current, "histogram", "bar", "red_bar")
    previous_bar = _value(previous, "histogram", "bar", "red_bar")
    if growing:
        relation = _comparison(current_bar, previous_bar, tolerance)
        active = current_bar > tolerance and relation > 0
    else:
        relation = _comparison(current_bar, previous_bar, tolerance)
        active = current_bar > tolerance and previous_bar > tolerance and relation < 0
    return StateSignal(active, active)


def _with_direction(*, narrowing: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _ma_gap(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        narrowing=narrowing,
    )


def _with_kdj_cross(*, up: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _kdj_cross(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        up=up,
    )


def _with_kdj_direction(*, rising: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _all_direction(
        current,
        previous,
        fields=("k", "d", "j"),
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        rising=rising,
    )


def _with_boll_width(*, narrowing: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _boll_width_state(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        narrowing=narrowing,
    )


def _with_boll_direction(*, rising: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _boll_all_direction(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        rising=rising,
    )


def _with_boll_break(*, upper: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _boll_break(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        upper=upper,
    )


def _with_macd_cross(*, up: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _macd_cross(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        up=up,
    )


def _with_macd_bar(*, growing: bool) -> StateEvaluator:
    return lambda current, previous, *, price, previous_price, tolerance: _macd_red_bar(
        current,
        previous,
        price=price,
        previous_price=previous_price,
        tolerance=tolerance,
        growing=growing,
    )


def _definition(
    code: str,
    name: str,
    level: str,
    indicator_type: str,
    *,
    mode: str,
    fields: tuple[str, ...],
    evaluator: StateEvaluator,
    comparison: str,
) -> StateDefinition:
    return StateDefinition(
        code=code,
        name=name,
        level=level,
        indicator_type=indicator_type,
        metadata={"mode": mode, "fields": fields, "comparison": comparison},
        evaluator=evaluator,
    )


def create_default_definitions() -> tuple[StateDefinition, ...]:
    """Return fresh built-in definitions in stable UI/alert order."""

    return (
        _definition(
            "MA_CROSS_UP",
            "MA上穿",
            "POSITIVE",
            "MA",
            mode="edge",
            fields=("short", "long"),
            comparison="cross_up",
            evaluator=_ma_cross_up,
        ),
        _definition(
            "MA_CROSS_DOWN",
            "MA下穿",
            "NEGATIVE",
            "MA",
            mode="edge",
            fields=("short", "long"),
            comparison="cross_down",
            evaluator=_ma_cross_down,
        ),
        _definition(
            "MA_GAP_NARROWING",
            "MA间距收窄",
            "WARNING",
            "MA",
            mode="continuous",
            fields=("short", "long"),
            comparison="gap_decrease",
            evaluator=_with_direction(narrowing=True),
        ),
        _definition(
            "MA_GAP_EXPANDING",
            "MA间距扩大",
            "INFO",
            "MA",
            mode="continuous",
            fields=("short", "long"),
            comparison="gap_increase",
            evaluator=_with_direction(narrowing=False),
        ),
        _definition(
            "KDJ_K_CROSS_D_UP",
            "K上穿D",
            "POSITIVE",
            "KDJ",
            mode="edge",
            fields=("k", "d"),
            comparison="cross_up",
            evaluator=_with_kdj_cross(up=True),
        ),
        _definition(
            "KDJ_K_CROSS_D_DOWN",
            "K下穿D",
            "NEGATIVE",
            "KDJ",
            mode="edge",
            fields=("k", "d"),
            comparison="cross_down",
            evaluator=_with_kdj_cross(up=False),
        ),
        _definition(
            "KDJ_ALL_RISING",
            "KDJ三线齐上",
            "POSITIVE",
            "KDJ",
            mode="continuous",
            fields=("k", "d", "j"),
            comparison="all_increase",
            evaluator=_with_kdj_direction(rising=True),
        ),
        _definition(
            "KDJ_ALL_FALLING",
            "KDJ三线齐下",
            "NEGATIVE",
            "KDJ",
            mode="continuous",
            fields=("k", "d", "j"),
            comparison="all_decrease",
            evaluator=_with_kdj_direction(rising=False),
        ),
        _definition(
            "BOLL_WIDTH_NARROWING",
            "带口收窄",
            "WARNING",
            "BOLL",
            mode="continuous",
            fields=("width",),
            comparison="decrease",
            evaluator=_with_boll_width(narrowing=True),
        ),
        _definition(
            "BOLL_WIDTH_EXPANDING",
            "带口扩大",
            "INFO",
            "BOLL",
            mode="continuous",
            fields=("width",),
            comparison="increase",
            evaluator=_with_boll_width(narrowing=False),
        ),
        _definition(
            "BOLL_ALL_RISING",
            "三线齐上",
            "POSITIVE",
            "BOLL",
            mode="continuous",
            fields=("upper", "middle", "lower"),
            comparison="all_increase",
            evaluator=_with_boll_direction(rising=True),
        ),
        _definition(
            "BOLL_ALL_FALLING",
            "三线齐下",
            "NEGATIVE",
            "BOLL",
            mode="continuous",
            fields=("upper", "middle", "lower"),
            comparison="all_decrease",
            evaluator=_with_boll_direction(rising=False),
        ),
        _definition(
            "BOLL_BREAK_UPPER",
            "突破上轨",
            "POSITIVE",
            "BOLL",
            mode="edge",
            fields=("upper", "price"),
            comparison="break_above",
            evaluator=_with_boll_break(upper=True),
        ),
        _definition(
            "BOLL_BREAK_LOWER",
            "跌破下轨",
            "NEGATIVE",
            "BOLL",
            mode="edge",
            fields=("lower", "price"),
            comparison="break_below",
            evaluator=_with_boll_break(upper=False),
        ),
        _definition(
            "MACD_DIFF_CROSS_DEA_UP",
            "DIFF上穿DEA",
            "POSITIVE",
            "MACD",
            mode="edge",
            fields=("diff", "dea"),
            comparison="cross_up",
            evaluator=_with_macd_cross(up=True),
        ),
        _definition(
            "MACD_DIFF_CROSS_DEA_DOWN",
            "DIFF下穿DEA",
            "NEGATIVE",
            "MACD",
            mode="edge",
            fields=("diff", "dea"),
            comparison="cross_down",
            evaluator=_with_macd_cross(up=False),
        ),
        _definition(
            "MACD_RED_BAR_GROWING",
            "红柱增长",
            "POSITIVE",
            "MACD",
            mode="continuous",
            fields=("histogram",),
            comparison="positive_increase",
            evaluator=_with_macd_bar(growing=True),
        ),
        _definition(
            "MACD_RED_BAR_SHRINKING",
            "红柱缩短",
            "NEGATIVE",
            "MACD",
            mode="continuous",
            fields=("histogram",),
            comparison="positive_decrease",
            evaluator=_with_macd_bar(growing=False),
        ),
    )
