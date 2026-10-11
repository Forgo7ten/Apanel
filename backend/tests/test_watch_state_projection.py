"""Regression coverage for exact column state identity, without a database."""

from datetime import date
from types import SimpleNamespace

from app.services.state_service import state_parameter_key
from app.services.watch_table_service import _state_matches_column, _visible_states


def state(code, indicator, params, status="ACTIVE"):
    return SimpleNamespace(
        state_code=code,
        indicator_type=indicator,
        parameters=params,
        status=status,
        parameter_key=state_parameter_key(code, params),
        trade_date=date(2026, 10, 10),
    )


def column(indicator, parameters, mode="COMPOSITE", code=None):
    return SimpleNamespace(
        indicator_type=indicator,
        column_type="INDICATOR",
        parameters=parameters,
        view_mode=mode,
        state_code=code,
    )


def test_custom_boll_only_matches_own_parameters():
    custom = state("BOLL_WIDTH_NARROWING", "BOLL", {"period": 30, "multiplier": 2.5})
    default = state("BOLL_WIDTH_NARROWING", "BOLL", {"period": 20, "multiplier": 2})
    view = column("BOLL", {"period": 30, "multiplier": 2.5})
    assert _state_matches_column(custom, view)
    assert not _state_matches_column(default, view)
    assert _visible_states([custom, default], [view]) == [custom, default]
    assert not _state_matches_column(custom, column("MACD", {}))
    assert not _state_matches_column(
        state(custom.state_code, "BOLL", custom.parameters, "INACTIVE"), view
    )


def test_ma_pair_and_default_pair_embedded_in_many_periods():
    pair = state("MA_CROSS_UP", "MA", {"short_period": 20, "long_period": 60})
    default = state("MA_CROSS_UP", "MA", {"short_period": 5, "long_period": 10})
    assert _state_matches_column(pair, column("MA", {"periods": [20, 60]}))
    assert not _state_matches_column(pair, column("MA", {"periods": [5, 10, 30]}))
    assert _state_matches_column(default, column("MA", {"periods": [5, 10, 30]}))
    assert not _state_matches_column(default, column("MA", {"period": 5}))


def test_explicit_status_state_matches_only_exact_state_code_and_key():
    item = state("BOLL_WIDTH_NARROWING", "BOLL", {"period": 30, "multiplier": 2.5})
    view = column("BOLL", item.parameters, "STATUS", item.state_code)
    assert _state_matches_column(item, view)
    assert not _state_matches_column(
        item, column("BOLL", item.parameters, "STATUS", "BOLL_WIDTH_EXPANDING")
    )
