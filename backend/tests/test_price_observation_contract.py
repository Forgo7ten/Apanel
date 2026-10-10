"""Observation timestamp is not a verified TDX exchange event timestamp."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.watch_table_service import _price_data


def quote(at):
    return SimpleNamespace(
        timestamp=at,
        price=Decimal("12.5"),
        change=Decimal("0.2"),
        change_percent=Decimal("1.6"),
    )


def test_recent_quote_is_labeled_observation_not_exchange_event():
    at = datetime(2026, 10, 9, 6, tzinfo=UTC)
    price = _price_data(quote(at), now=at + timedelta(minutes=10))
    assert price.price == 12.5
    assert price.timestamp_kind == "OBSERVED_AT"
    assert price.observation_stale is False


def test_observation_older_than_twenty_minutes_is_explicitly_marked_stale():
    at = datetime(2026, 10, 9, 6, tzinfo=UTC)
    assert _price_data(quote(at), now=at + timedelta(minutes=21)).observation_stale is True
    assert _price_data(quote(at), now=at + timedelta(minutes=20)).observation_stale is False


def test_daily_close_fallback_is_a_trade_date_not_quote_observation():
    at = datetime(2026, 10, 9, 6, tzinfo=UTC)
    daily_bar = SimpleNamespace(trade_date=date(2026, 10, 9), close=Decimal("12.6"))
    price = _price_data(quote(at), daily_bar, now=at + timedelta(minutes=10))
    assert price.price == 12.6
    assert price.timestamp_kind == "DAILY_BAR_DATE"
    assert price.observation_stale is None


def test_missing_quote_and_daily_bar_is_unavailable():
    assert _price_data(None) is None
