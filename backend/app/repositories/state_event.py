"""Compact State timeline; all writes participate in the caller's stream transaction."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import AnalysisStream, CurrentIndicatorState, StateEvent
from app.repositories.indicator_state import IndicatorStateRepository
from app.schemas.state_row import StateRow


def observation(row: Any, *, trade_date: date | None = None) -> StateRow:
    """The old read DTO remains valid; these transient objects are never added to Session."""
    data = (
        dict(row)
        if isinstance(row, dict)
        else {
            field: getattr(row, field)
            for field in (
                "id",
                "security_id",
                "adjust_type",
                "state_code",
                "parameter_key",
                "indicator_type",
                "parameters",
                "status",
                "trade_date",
                "since_date",
                "observed_from",
                "since_is_exact",
                "source_revision",
            )
        }
    )
    metadata = data.pop("metadata", None) if isinstance(row, dict) else row.metadata_json
    since = {
        key: data.pop(key, None)
        for key in (
            "since_date",
            "observed_from",
            "since_is_exact",
            "source_revision",
        )
    }
    data["metadata"] = dict(metadata or {})
    if trade_date is not None:
        data["trade_date"] = trade_date
    result = StateRow(**data)
    for key, value in since.items():
        setattr(result, key, value)
    return result


def _target(row):
    return row.state_code, row.parameter_key


def _signature(payload):
    fields = (
        "trade_date",
        "state_code",
        "parameter_key",
        "indicator_type",
        "status",
        "event_kind",
        "since_date",
        "observed_from",
        "since_is_exact",
        "parameters",
        "metadata",
    )
    return json.dumps(
        {key: payload[key] for key in fields},
        sort_keys=True,
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _event_payload(row):
    return {
        **{
            key: getattr(row, key)
            for key in (
                "trade_date",
                "state_code",
                "parameter_key",
                "indicator_type",
                "status",
                "event_kind",
                "since_date",
                "observed_from",
                "since_is_exact",
                "parameters",
            )
        },
        "metadata": dict(row.metadata_json),
    }


class StateEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def current_many(
        self,
        security_ids,
        *,
        adjustment="qfq",
        active_only=False,
        state_code=None,
        parameter_key=None,
    ):
        """Read only the compact, published Current State store.

        Unconverted streams are not silently interpreted using stale daily
        observations. They must be migrated or rebuilt before being readable.
        """
        ids = tuple(sorted(set(security_ids)))
        if not ids:
            return {}
        query = select(
            CurrentIndicatorState.id,
            CurrentIndicatorState.security_id,
            CurrentIndicatorState.adjust_type,
            CurrentIndicatorState.state_code,
            CurrentIndicatorState.parameter_key,
            CurrentIndicatorState.indicator_type,
            CurrentIndicatorState.parameters,
            CurrentIndicatorState.status,
            CurrentIndicatorState.trade_date,
            CurrentIndicatorState.metadata_json.label("metadata"),
            CurrentIndicatorState.since_date,
            CurrentIndicatorState.observed_from,
            CurrentIndicatorState.since_is_exact,
            CurrentIndicatorState.source_revision,
        ).where(
            CurrentIndicatorState.security_id.in_(ids),
            CurrentIndicatorState.adjust_type == adjustment,
        )
        if active_only:
            query = query.where(CurrentIndicatorState.status == "ACTIVE")
        if state_code is not None:
            query = query.where(CurrentIndicatorState.state_code == state_code)
        if parameter_key is not None:
            query = query.where(CurrentIndicatorState.parameter_key == parameter_key)
        query = query.order_by(
            CurrentIndicatorState.security_id,
            CurrentIndicatorState.state_code,
            CurrentIndicatorState.parameter_key,
        )
        rows = (await self.session.execute(query)).mappings().all()
        result = {sid: [] for sid in ids}
        for row in rows:
            result[row["security_id"]].append(observation(dict(row)))
        return result

    async def seed_at(self, security_id, adjustment, day: date) -> list[StateRow]:
        """Last confirmed event at/before day for EVERY target, including range anchors."""
        ranked = (
            select(
                StateEvent.id,
                func.row_number()
                .over(
                    partition_by=(StateEvent.state_code, StateEvent.parameter_key),
                    order_by=(StateEvent.trade_date.desc(), StateEvent.stream_seq.desc()),
                )
                .label("rank"),
            )
            .where(
                StateEvent.security_id == security_id,
                StateEvent.adjust_type == adjustment,
                StateEvent.valid.is_(True),
                StateEvent.trade_date <= day,
            )
            .subquery()
        )
        rows = (
            (
                await self.session.execute(
                    select(StateEvent)
                    .join(ranked, StateEvent.id == ranked.c.id)
                    .where(ranked.c.rank == 1)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        return [observation(row, trade_date=day) for row in rows]

    async def publish(
        self,
        stream: AnalysisStream,
        observations: list[StateRow],
        *,
        start: date | None,
        through: date,
    ) -> int:
        """Replace a valid timeline suffix and its current snapshot atomically.

        Old revised events are retained with valid=false for audit. Identical
        replays preserve event IDs and sequence numbers. No daily inactive rows.
        The caller holds the stream lock; sequence allocation commits with events.
        """
        # Avoid importing the services package while this repository module is
        # still being initialized (the service registry imports AlertService).
        from app.services.analysis_control import insert_for

        existing = (
            (
                await self.session.execute(
                    select(CurrentIndicatorState).where(
                        CurrentIndicatorState.security_id == stream.security_id,
                        CurrentIndicatorState.adjust_type == stream.adjust_type,
                    )
                )
            )
            .scalars()
            .all()
        )
        previous_through = max((row.trade_date for row in existing), default=None)
        if previous_through is not None and through < previous_through:
            raise ApiError(
                "STALE_ANALYSIS_TARGET", "Cannot regress the current State snapshot.", 409
            )
        ordered = sorted(
            observations, key=lambda row: (row.trade_date, row.state_code, row.parameter_key)
        )
        replace_from = start or min((row.trade_date for row in ordered), default=through)
        # Full rebuild intentionally starts without old facts. Partial rebuild
        # anchors strictly BEFORE the first rewritten day, never within it.
        if start is not None:
            from datetime import timedelta

            anchors = await self.seed_at(
                stream.security_id, stream.adjust_type, replace_from - timedelta(days=1)
            )
        else:
            anchors = []
        current = {
            _target(row): {
                "status": row.status,
                "since_date": row.since_date,
                "observed_from": row.observed_from,
                "since_is_exact": row.since_is_exact,
            }
            for row in anchors
        }
        last = {}
        events = []
        for row in ordered:
            key = _target(row)
            prior = current.get(key)
            metadata = dict(row.metadata_json)
            kind = None
            provable_edge = bool(metadata.get("transition"))
            if prior is None:
                kind = "BASELINE"
            elif row.status != prior["status"]:
                if row.status == "UNKNOWN":
                    kind = "UNKNOWN"
                elif prior["status"] == "UNKNOWN":
                    kind = "ENTER" if row.status == "ACTIVE" and provable_edge else "RECOVER"
                else:
                    kind = "ENTER" if row.status == "ACTIVE" else "EXIT"
            if prior is None or kind is not None:
                exact = kind in {"ENTER", "EXIT"}
                since = row.trade_date if exact else None
                observed = row.trade_date
            else:
                exact, since, observed = (
                    prior["since_is_exact"],
                    prior["since_date"],
                    prior["observed_from"],
                )
            values = dict(
                security_id=stream.security_id,
                adjust_type=stream.adjust_type,
                state_code=row.state_code,
                parameter_key=row.parameter_key,
                indicator_type=row.indicator_type,
                parameters=dict(row.parameters),
                status=row.status,
                trade_date=row.trade_date,
                since_date=since,
                observed_from=observed,
                since_is_exact=exact,
                source_revision=stream.source_revision,
                metadata=metadata,
            )
            current[key] = values
            last[key] = values
            if kind is not None:
                events.append({**values, "event_kind": kind})
        query = select(StateEvent).where(
            StateEvent.security_id == stream.security_id,
            StateEvent.adjust_type == stream.adjust_type,
            StateEvent.valid.is_(True),
            StateEvent.trade_date <= through,
        )
        if start is not None:
            query = query.where(StateEvent.trade_date >= replace_from)
        old_events = (
            (
                await self.session.execute(
                    query.order_by(
                        StateEvent.trade_date, StateEvent.state_code, StateEvent.parameter_key
                    )
                )
            )
            .scalars()
            .all()
        )
        unchanged = [_signature(_event_payload(row)) for row in old_events] == [
            _signature(row) for row in events
        ]
        written = 0
        if not unchanged or previous_through != through:
            # A default date window also moves when the current observation
            # advances without an event. Its pagination must be invalidated.
            stream.state_generation += 1
        if not unchanged:
            # Invalidating the last event may insert zero replacements. Event
            # sequence then stays fixed, but readers must restart pagination.
            filters = [
                StateEvent.security_id == stream.security_id,
                StateEvent.adjust_type == stream.adjust_type,
                StateEvent.valid.is_(True),
                StateEvent.trade_date <= through,
            ]
            if start is not None:
                filters.append(StateEvent.trade_date >= replace_from)
            await self.session.execute(update(StateEvent).where(*filters).values(valid=False))
            payloads = []
            for value in events:
                stream.event_sequence += 1
                # Historical replacements must not masquerade as new live edges.
                restated = previous_through is None or value["trade_date"] <= previous_through
                payloads.append(
                    {
                        **value,
                        "stream_seq": stream.event_sequence,
                        "valid": True,
                        "is_restatement": restated,
                    }
                )
            for offset in range(0, len(payloads), 300):
                values = [
                    {
                        ("metadata_json" if key == "metadata" else key): value
                        for key, value in row.items()
                    }
                    for row in payloads[offset : offset + 300]
                ]
                await self.session.execute(insert_for(self.session, StateEvent).values(values))
            written = len(payloads)
        # Full rebuild drops no-longer-demanded targets. Partial rebuild updates
        # only recomputed targets, preserving independent snapshots otherwise.
        if start is None:
            await self.session.execute(
                delete(CurrentIndicatorState).where(
                    CurrentIndicatorState.security_id == stream.security_id,
                    CurrentIndicatorState.adjust_type == stream.adjust_type,
                )
            )
        if last:
            payloads = [{**value, "updated_at": datetime.now(UTC)} for value in last.values()]
            await IndicatorStateRepository(self.session)._upsert_many(
                CurrentIndicatorState,
                payloads,
                ("security_id", "adjust_type", "state_code", "parameter_key"),
                (
                    "indicator_type",
                    "parameters",
                    "status",
                    "trade_date",
                    "since_date",
                    "observed_from",
                    "since_is_exact",
                    "source_revision",
                    "metadata",
                    "updated_at",
                ),
            )
        return written

    async def events(
        self,
        security_id,
        adjustment,
        *,
        start,
        end,
        state_code=None,
        parameter_key=None,
        indicator_type=None,
        after_seq=0,
        limit=1000,
    ):
        query = select(StateEvent).where(
            StateEvent.security_id == security_id,
            StateEvent.adjust_type == adjustment,
            StateEvent.valid.is_(True),
            StateEvent.trade_date >= start,
            StateEvent.trade_date <= end,
            StateEvent.stream_seq > after_seq,
        )
        for column, value in (
            (StateEvent.state_code, state_code),
            (StateEvent.parameter_key, parameter_key),
            (StateEvent.indicator_type, indicator_type),
        ):
            if value is not None:
                query = query.where(column == value)
        return list(
            (
                await self.session.execute(query.order_by(StateEvent.stream_seq).limit(limit))
            ).scalars()
        )
