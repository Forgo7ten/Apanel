"""Pure price-adjustment helpers used by provider adapters.

The provider boundary deals in raw mappings, while the rest of the service
deals in validated domain records.  Keeping the forward-adjustment algorithm
here makes it deterministic and independently testable without a TDX socket
or a database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .market_data import InvalidMarketDataError

_PRICE_FIELDS = ("open", "high", "low", "close")
_MISSING = object()


def apply_qfq(
    records: Sequence[Mapping[str, Any]],
    corporate_actions: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """Return records transformed to forward-adjusted (qfq) prices.

    ``corporate_actions`` may provide an explicit ``factor`` (or
    ``adjustment_factor``/``qfq_factor``).  For cash dividends, the factor is
    derived as ``(previous_close - cash_amount) / previous_close``.  The
    factor applies to bars strictly before the ex-dividend date, leaving the
    current/ex-date price at its raw value, which is the usual A-share qfq
    convention.

    The function intentionally does not mutate provider-owned mappings.  It
    preserves volume and amount because those values describe the raw traded
    quantity/turnover; only OHLC prices and the adjustment marker change.
    """

    prepared = []
    for record in records:
        if not isinstance(record, Mapping):
            raise InvalidMarketDataError("qfq input bar must be a mapping")
        bar_date = _date_value(_record_value(record, "trade_date", "date", "datetime"))
        for field in _PRICE_FIELDS:
            _decimal(_record_value(record, field), field)
        prepared.append((bar_date, dict(record)))

    prepared.sort(key=lambda item: item[0])
    if not prepared:
        return ()
    factors_by_date = _action_factors(corporate_actions, prepared)

    result: list[dict[str, Any]] = []
    for bar_date, record in prepared:
        multiplier = Decimal("1")
        for event_date, factor in factors_by_date.items():
            if event_date > bar_date:
                multiplier *= factor
        for field in _PRICE_FIELDS:
            record[field] = _decimal(record[field], field) * multiplier
        record["adjustment"] = "qfq"
        result.append(record)
    return tuple(result)


# Descriptive aliases keep the algorithm discoverable to provider callers.
forward_adjust_bars = apply_qfq
adjust_forward = apply_qfq


def _action_factors(
    actions: Sequence[Mapping[str, Any]],
    bars: Sequence[tuple[date, Mapping[str, Any]]],
) -> dict[date, Decimal]:
    factors: dict[date, Decimal] = {}
    for action in actions:
        if not isinstance(action, Mapping):
            raise InvalidMarketDataError("qfq corporate action must be a mapping")
        event_date = _date_value(_record_value(action, "date", "ex_date", "trade_date"))
        explicit_factor = _record_value(
            action,
            "factor",
            "adjustment_factor",
            "qfq_factor",
            default=None,
        )
        if explicit_factor is not None:
            factor = _decimal(explicit_factor, "adjustment_factor")
        else:
            cash_amount = _record_value(action, "cash_amount", "cash", "fenhong", default=None)
            if cash_amount is None:
                raise InvalidMarketDataError(
                    "qfq corporate action needs factor or cash_amount"
                )
            cash = _decimal(cash_amount, "cash_amount")
            if cash < 0:
                raise InvalidMarketDataError("cash_amount must not be negative")
            previous_close = _record_value(
                action,
                "previous_close",
                "prev_close",
                "pre_close",
                "close_before",
                default=None,
            )
            if previous_close is None:
                previous_close = _bar_before(bars, event_date)
            if previous_close is None:
                raise InvalidMarketDataError(
                    f"qfq action on {event_date.isoformat()} is missing previous close"
                )
            previous = _decimal(previous_close, "previous_close")
            if previous <= 0:
                raise InvalidMarketDataError("previous_close must be greater than zero")
            factor = (previous - cash) / previous
        if not factor.is_finite() or factor <= 0:
            raise InvalidMarketDataError("adjustment_factor must be finite and greater than zero")
        factors[event_date] = factors.get(event_date, Decimal("1")) * factor
    return factors


def _bar_before(
    bars: Sequence[tuple[date, Mapping[str, Any]]],
    event_date: date,
) -> Any:
    previous = None
    for bar_date, record in bars:
        if bar_date >= event_date:
            break
        previous = _record_value(record, "close")
    return previous


def _record_value(record: object, *keys: str, default: Any = _MISSING) -> Any:
    for key in keys:
        if isinstance(record, Mapping) and key in record:
            value = record[key]
        else:
            value = getattr(record, key, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    if default is not _MISSING:
        return default
    joined = "/".join(keys)
    raise InvalidMarketDataError(f"qfq record is missing {joined}")


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidMarketDataError(f"{field} must be a finite decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidMarketDataError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise InvalidMarketDataError(f"{field} must be a finite decimal")
    return result


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip().replace("/", "-")[:10])
        except ValueError as exc:
            raise InvalidMarketDataError("qfq date must be an ISO calendar date") from exc
    raise InvalidMarketDataError("qfq record is missing a valid date")


__all__ = ["adjust_forward", "apply_qfq", "forward_adjust_bars"]
