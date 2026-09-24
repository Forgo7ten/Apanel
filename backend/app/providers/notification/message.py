"""Notification message value object shared by provider adapters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from numbers import Real
from typing import Any


def _finite_value(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError(f"{field_name} must be a real number or None")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be a real number") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


def _text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True, init=False)
class NotificationMessage:
    """The provider-neutral content of one alert notification.

    ``date`` is deliberately a calendar date rather than a provider-specific
    timestamp.  A later notification service can add delivery metadata while
    retaining this stable content contract.
    """

    stock_name: str
    stock_code: str
    indicator: str
    state: str | None
    current_value: float | None
    previous_value: float | None
    change: float | None
    date: Date

    def __init__(
        self,
        stock_name: str,
        stock_code: str | None = None,
        indicator: str = "",
        state: str | None = None,
        current_value: Real | Decimal | None = None,
        previous_value: Real | Decimal | None = None,
        change: Real | Decimal | None = None,
        date: Date | datetime | None = None,
        *,
        symbol: str | None = None,
        state_id: str | None = None,
        trade_date: Date | datetime | None = None,
    ) -> None:
        if stock_code is not None and symbol is not None and stock_code != symbol:
            raise ValueError("stock_code and symbol do not match")
        resolved_code = stock_code if stock_code is not None else symbol
        if resolved_code is None:
            raise ValueError("stock_code is required")
        if state is not None and state_id is not None and state.strip() != state_id.strip():
            raise ValueError("state and state_id do not match")
        resolved_state = state if state is not None else state_id
        if date is not None and trade_date is not None:
            first = date.date() if isinstance(date, datetime) else date
            second = trade_date.date() if isinstance(trade_date, datetime) else trade_date
            if first != second:
                raise ValueError("date and trade_date do not match")
        resolved_date = date if date is not None else trade_date
        if resolved_date is None:
            raise ValueError("date is required")
        if isinstance(resolved_date, datetime):
            resolved_date = resolved_date.date()
        if not isinstance(resolved_date, Date):
            raise ValueError("date must be a date")
        normalized_current = _finite_value(current_value, field_name="current_value")
        normalized_previous = _finite_value(previous_value, field_name="previous_value")
        normalized_change = _finite_value(change, field_name="change")
        if (
            normalized_change is None
            and normalized_current is not None
            and normalized_previous is not None
        ):
            normalized_change = normalized_current - normalized_previous
        object.__setattr__(self, "stock_name", _text(stock_name, field_name="stock_name"))
        object.__setattr__(self, "stock_code", _text(resolved_code, field_name="stock_code"))
        object.__setattr__(self, "indicator", _text(indicator, field_name="indicator"))
        object.__setattr__(
            self,
            "state",
            None if resolved_state is None else _text(resolved_state, field_name="state"),
        )
        object.__setattr__(self, "current_value", normalized_current)
        object.__setattr__(self, "previous_value", normalized_previous)
        object.__setattr__(self, "change", normalized_change)
        object.__setattr__(self, "date", resolved_date)

    @property
    def symbol(self) -> str:
        """Market-data vocabulary alias for ``stock_code``."""

        return self.stock_code

    @property
    def state_id(self) -> str | None:
        return self.state

    @property
    def trade_date(self) -> Date:
        return self.date

    @property
    def title(self) -> str:
        return self.state or self.indicator

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_name": self.stock_name,
            "stock_code": self.stock_code,
            "indicator": self.indicator,
            "state": self.state,
            "current_value": self.current_value,
            "previous_value": self.previous_value,
            "change": self.change,
            "date": self.date.isoformat(),
        }

    def render_text(self) -> str:
        """Render a compact, human-readable message for webhook providers."""

        lines = [
            f"{self.stock_name}（{self.stock_code}）",
            f"指标：{self.indicator}",
        ]
        if self.state is not None:
            lines.append(f"状态：{self.state}")
        if self.current_value is not None:
            lines.append(f"当前值：{self.current_value:g}")
        if self.previous_value is not None:
            lines.append(f"上次值：{self.previous_value:g}")
        if self.change is not None:
            lines.append(f"变化：{self.change:+g}")
        lines.append(f"日期：{self.date.isoformat()}")
        return "\n".join(lines)


__all__ = ["NotificationMessage"]
