"""Persist precise alert observation targets."""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_alert_target"
down_revision: str | Sequence[str] | None = "0007_adjustment_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alert_rules", sa.Column("parameters", sa.JSON(), nullable=True))
    op.add_column("alert_rules", sa.Column("parameter_key", sa.String(length=67), nullable=True))
    op.add_column("alert_rules", sa.Column("field", sa.String(length=64), nullable=True))
    op.add_column(
        "alert_rules",
        sa.Column("adjust_type", sa.String(length=8), nullable=False, server_default="qfq"),
    )
    op.drop_constraint("ck_alert_rules_condition_fields", "alert_rules", type_="check")
    op.create_check_constraint(
        "ck_alert_rules_condition_fields",
        "alert_rules",
        "((condition_type = 'VALUE' AND indicator_type IS NOT NULL AND operator IS NOT NULL "
        "AND threshold IS NOT NULL AND state_code IS NULL) OR "
        "(condition_type = 'STATE' AND state_code IS NOT NULL AND operator IS NULL "
        "AND threshold IS NULL))",
    )
    op.execute("UPDATE alert_rules SET adjust_type='qfq' WHERE adjust_type IS NULL")
    bind = op.get_bind()
    rows = (
        bind.execute(
            sa.text("SELECT id, condition_type, indicator_type, state_code FROM alert_rules")
        )
        .mappings()
        .all()
    )
    defaults = {
        "RSI": ({"period": 14}, "value"),
        "PROJECTED_MA": ({"period": 5}, "value"),
        "DIVIDEND_YIELD": ({}, "value"),
    }
    state_defaults = {
        "KDJ": {"d_period": 3, "k_period": 3, "period": 9},
        "BOLL": {"multiplier": 2.0, "period": 20},
        "MACD": {"fast_period": 12, "signal_period": 9, "slow_period": 26},
    }
    for row in rows:
        if row["condition_type"] == "VALUE":
            indicator = str(row["indicator_type"] or "").upper()
            if indicator not in defaults:
                bind.execute(
                    sa.text("UPDATE alert_rules SET enabled=false WHERE id=:id"), {"id": row["id"]}
                )
                continue
            params, field = defaults[indicator]
            adjust = "none" if indicator == "DIVIDEND_YIELD" else "qfq"
            bind.execute(
                sa.text(
                    "UPDATE alert_rules SET parameters=:params, parameter_key=:key, "
                    "field=:field, adjust_type=:adjust WHERE id=:id"
                ),
                {
                    "params": json.dumps(params),
                    "key": _parameter_key(indicator, params),
                    "field": field,
                    "adjust": adjust,
                    "id": row["id"],
                },
            )
        else:
            code = str(row["state_code"] or "").upper()
            indicator = code.split("_", 1)[0]
            if indicator == "MA":
                params = {"long_period": 10, "short_period": 5}
            else:
                params = state_defaults.get(indicator, {})
            bind.execute(
                sa.text(
                    "UPDATE alert_rules SET indicator_type=:indicator, parameters=:params, "
                    "parameter_key=:key, adjust_type='qfq' WHERE id=:id"
                ),
                {
                    "indicator": indicator,
                    "params": json.dumps(params),
                    "key": _state_parameter_key(code, params),
                    "id": row["id"],
                },
            )


def _parameter_key(indicator: str, parameters: dict) -> str:
    encoded = json.dumps(
        {"indicator_type": indicator, "parameters": parameters},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"v1_{hashlib.sha256(encoded).hexdigest()}"


def _state_parameter_key(state_code: str, parameters: dict) -> str:
    encoded = json.dumps(
        {"state_code": state_code, "parameters": parameters},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"v1_{hashlib.sha256(encoded).hexdigest()}"


def downgrade() -> None:
    op.drop_constraint("ck_alert_rules_condition_fields", "alert_rules", type_="check")
    op.create_check_constraint(
        "ck_alert_rules_condition_fields",
        "alert_rules",
        "((condition_type = 'VALUE' AND indicator_type IS NOT NULL AND operator IS NOT NULL "
        "AND threshold IS NOT NULL AND state_code IS NULL) OR "
        "(condition_type = 'STATE' AND state_code IS NOT NULL AND indicator_type IS NULL "
        "AND operator IS NULL AND threshold IS NULL))",
    )
    for column in ("adjust_type", "field", "parameter_key", "parameters"):
        op.drop_column("alert_rules", column)
