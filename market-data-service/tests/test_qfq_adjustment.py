from datetime import date
from decimal import Decimal

import pytest

from app.domain.adjustments import apply_qfq
from app.domain.market_data import InvalidMarketDataError


def _bar(day: int, value: str) -> dict[str, object]:
    return {
        "trade_date": date(2026, 9, day),
        "open": value,
        "high": str(Decimal(value) + Decimal("1")),
        "low": str(Decimal(value) - Decimal("1")),
        "close": value,
        "volume": "100",
    }


def test_apply_qfq_scales_only_bars_before_cash_dividend() -> None:
    records = apply_qfq(
        [_bar(22, "10"), _bar(23, "9")],
        [{"date": date(2026, 9, 23), "cash_amount": "1", "previous_close": "10"}],
    )

    assert records[0]["close"] == Decimal("9.0")
    assert records[0]["open"] == Decimal("9.0")
    assert records[1]["close"] == Decimal("9")
    assert all(record["adjustment"] == "qfq" for record in records)


def test_apply_qfq_supports_explicit_factor_and_does_not_mutate_input() -> None:
    original = _bar(22, "10")
    records = apply_qfq([original], [{"date": date(2026, 9, 23), "factor": "0.8"}])

    assert original["close"] == "10"
    assert records[0]["close"] == Decimal("8.0")


def test_apply_qfq_rejects_an_action_without_a_derivable_factor() -> None:
    with pytest.raises(InvalidMarketDataError, match="previous close"):
        apply_qfq([_bar(22, "10")], [{"date": date(2026, 9, 21), "cash_amount": "1"}])
