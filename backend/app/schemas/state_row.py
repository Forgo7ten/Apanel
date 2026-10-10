"""Transient state observation for calculation and API projection.

Unlike the retired daily-state ORM entity this object is never persisted.
Only CurrentIndicatorState and StateEvent are database-backed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(init=False, slots=True)
class StateRow:
    security_id: int
    trade_date: date
    state_code: str
    indicator_type: str
    status: str
    parameters: dict[str, Any] = field(default_factory=dict)
    parameter_key: str = "default"
    adjust_type: str = "qfq"
    metadata_json: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    since_date: date | None = None
    observed_from: date | None = None
    since_is_exact: bool = False
    source_revision: int | None = None

    def __init__(
        self,
        *,
        security_id: int,
        trade_date: date,
        state_code: str,
        indicator_type: str,
        status: str,
        parameters: dict[str, Any] | None = None,
        parameter_key: str = "default",
        adjust_type: str = "qfq",
        metadata: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        id: int | None = None,
        since_date: date | None = None,
        observed_from: date | None = None,
        since_is_exact: bool = False,
        source_revision: int | None = None,
    ):
        if metadata is not None and metadata_json is not None:
            raise ValueError("Supply metadata or metadata_json, not both.")
        self.security_id = security_id
        self.trade_date = trade_date
        self.state_code = state_code
        self.indicator_type = indicator_type
        self.status = status
        self.parameters = dict(parameters or {})
        self.parameter_key = parameter_key
        self.adjust_type = adjust_type
        self.metadata_json = dict(metadata if metadata is not None else metadata_json or {})
        self.id = id
        self.since_date = since_date
        self.observed_from = observed_from
        self.since_is_exact = since_is_exact
        self.source_revision = source_revision

    @property
    def metadata(self) -> dict[str, Any]:
        return self.metadata_json
