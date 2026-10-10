"""Catch up a previously initialized rule without treating corrections as new signals."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.core.errors import ApiError
from app.indicators.parameters import select_snapshot_variant
from app.models import AnalysisStream, IndicatorSnapshot, StateEvent
from app.repositories.state_event import observation
from app.services.alert_observation_service import _field, _state_value_context

MAX_CATCHUP_ROWS = 10_000


def _restated(snapshot, day):
    source = (snapshot.parameters or {}).get("_source", {})
    cutoff = source.get("restated_through") if isinstance(source, dict) else None
    if cutoff is None:
        return False
    try:
        return day <= date.fromisoformat(cutoff)
    except (TypeError, ValueError):
        # Invalid provenance is not proof of a fresh signal.
        return True


async def catch_up(service, rule, instance, current_context, current_observation):
    """Caller owns the rule lock and a shared publication lock.

    All outbox rows, monotone trigger sequence and the consumer watermark commit
    together. A retry reads the cursor and cannot re-enqueue the same timeline.
    """
    from app.services.alert_service import AlertEvaluationResult, _domain_rule

    domain = _domain_rule(rule, registry=service.state_registry)
    first = instance.last_evaluated_trade_date + timedelta(days=1)
    last = current_context["date"]
    notifications = []
    if rule.condition_type == "STATE":
        stream = await service.session.get(AnalysisStream, (rule.security_id, rule.adjust_type))
        if stream is None or stream.state_through is None:
            return None  # Stream not published yet; no daily State fallback exists.
        events = list(
            (
                await service.session.execute(
                    select(StateEvent)
                    .where(
                        StateEvent.security_id == rule.security_id,
                        StateEvent.adjust_type == rule.adjust_type,
                        StateEvent.state_code == rule.state_code,
                        StateEvent.parameter_key == rule.parameter_key,
                        StateEvent.valid.is_(True),
                        StateEvent.trade_date >= first,
                        StateEvent.trade_date <= last,
                        StateEvent.stream_seq > instance.last_consumed_state_seq,
                    )
                    .order_by(StateEvent.trade_date, StateEvent.stream_seq)
                    .limit(MAX_CATCHUP_ROWS + 1)
                )
            ).scalars()
        )
        if len(events) > MAX_CATCHUP_ROWS:
            raise ApiError(
                "ALERT_HISTORY_TOO_LARGE", "Alert catch-up range exceeds the safety limit.", 409
            )
        edge_mode = service.state_registry.get(rule.state_code).metadata.get("mode") == "edge"
        for event in events:
            if event.status == "UNKNOWN":
                continue
            active = event.status == "ACTIVE"
            enter = event.event_kind == "ENTER" and not event.is_restatement
            # ENTER is proof of a new occurrence. Do not rely on a stale alert
            # active bit when intermediate EXIT observations were missed.
            evaluation = service.evaluator.evaluate(
                domain,
                active if not edge_mode else enter,
                previous_state="RESET" if enter else instance.status,
            )
            instance.status = evaluation.status.value
            if not enter:
                continue
            state = observation(event)
            context = {
                "date": event.trade_date,
                "current_value": None,
                "previous_value": None,
                "change": None,
                "snapshot": None,
                "state": state,
                "value_details": _state_value_context(event.metadata_json),
                "source_event_id": event.id,
                "source_revision": event.source_revision,
                "catch_up": event.trade_date < last,
            }
            record = await service._enqueue_trigger(rule, instance, context)
            notifications.append(record.id)
        instance.last_consumed_state_seq = stream.event_sequence
        if current_observation is not None:
            instance.status = service.evaluator.evaluate(
                domain, current_observation, previous_state=instance.status
            ).status.value
    else:
        snapshots = list(
            (
                await service.session.execute(
                    select(IndicatorSnapshot)
                    .where(
                        IndicatorSnapshot.security_id == rule.security_id,
                        IndicatorSnapshot.adjust_type == rule.adjust_type,
                        IndicatorSnapshot.indicator_type == rule.indicator_type,
                        IndicatorSnapshot.trade_date >= first,
                        IndicatorSnapshot.trade_date <= last,
                    )
                    .order_by(IndicatorSnapshot.trade_date)
                    .limit(MAX_CATCHUP_ROWS + 1)
                )
            ).scalars()
        )
        if len(snapshots) > MAX_CATCHUP_ROWS:
            raise ApiError(
                "ALERT_HISTORY_TOO_LARGE", "Alert catch-up range exceeds the safety limit.", 409
            )
        for snapshot in snapshots:
            variant = select_snapshot_variant(snapshot, rule.indicator_type, rule.parameters)
            if variant is None or variant.key != rule.parameter_key:
                continue
            value = _field(variant.values, rule.field)
            if value is None:
                continue
            evaluation = service.evaluator.evaluate(
                domain, {"value": value}, previous_state=instance.status
            )
            instance.status = evaluation.status.value
            if not evaluation.triggered or _restated(snapshot, snapshot.trade_date):
                continue
            source = snapshot.parameters.get("_source", {})
            context = {
                "date": snapshot.trade_date,
                "current_value": value,
                "previous_value": _field(variant.previous_values, rule.field),
                "change": _field(variant.delta, rule.field),
                "snapshot": snapshot,
                "state": None,
                "source_revision": source.get("revision") if isinstance(source, dict) else None,
                "catch_up": snapshot.trade_date < last,
            }
            record = await service._enqueue_trigger(rule, instance, context)
            notifications.append(record.id)
    instance.last_evaluated_trade_date = last
    await service.session.commit()
    return AlertEvaluationResult(
        rule.id,
        instance.status,
        bool(notifications),
        notifications[-1] if notifications else None,
        notification_ids=tuple(notifications),
    )
