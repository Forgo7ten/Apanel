"""Application services for alert CRUD, Edge Trigger evaluation and delivery."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.alerts import (
    AlertEvaluator,
    StateCondition,
    ValueCondition,
)
from app.alerts import (
    AlertRule as DomainAlertRule,
)
from app.core.errors import ApiError
from app.indicators.parameters import normalize_indicator_type, select_snapshot_variant
from app.models import (
    AlertInstance,
    AlertInstanceStatus,
    AlertRule,
    IndicatorSnapshot,
    IndicatorState,
    Notification,
    Security,
)
from app.providers.notification import (
    NotificationMessage,
    NotificationProvider,
    NotificationProviderRegistry,
)
from app.repositories.alert import AlertRepository
from app.repositories.notification import NotificationRepository
from app.schemas.alerts import (
    AlertRuleCreateRequest,
    AlertRuleData,
    AlertRuleUpdateRequest,
    NotificationData,
)
from app.schemas.security import SecurityData
from app.services.notification_service import NotificationService, ProviderFactory
from app.states import DEFAULT_REGISTRY, StateRegistry, UnknownStateError


@dataclass(frozen=True, slots=True)
class AlertEvaluationResult:
    """Small scheduler-facing result for one evaluated rule."""

    rule_id: int
    status: str
    triggered: bool
    notification_id: int | None = None


@dataclass(frozen=True, slots=True)
class _SnapshotObservation:
    """Default-parameter view of one v1/v2 persisted snapshot."""

    trade_date: date
    indicator_type: str
    parameters: dict[str, Any]
    values: dict[str, float]
    previous_values: dict[str, float] | None
    delta: dict[str, float] | None


class AlertService:
    """Keep HTTP-free alert rules and notification orchestration in one service."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        notification_provider: NotificationProvider | None = None,
        provider_factory: ProviderFactory | None = None,
        provider_registry: NotificationProviderRegistry | None = None,
        state_registry: StateRegistry | None = None,
    ) -> None:
        self.session = session
        self.repository = AlertRepository(session)
        self.notification_repository = NotificationRepository(session)
        self.state_registry = state_registry or DEFAULT_REGISTRY
        self.notification_service = NotificationService(
            session,
            provider=notification_provider,
            provider_factory=provider_factory,
            provider_registry=provider_registry,
        )
        self.evaluator = AlertEvaluator()

    async def list(self, user_id: int) -> list[AlertRuleData]:
        return [_rule_data(item) for item in await self.repository.list_for_user(user_id)]

    async def create(self, user_id: int, payload: AlertRuleCreateRequest) -> AlertRuleData:
        security = await self.session.get(Security, payload.security_id)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        fields = _condition_fields(
            condition_type=payload.condition_type,
            state_id=payload.state_id,
            state_code=payload.state_code,
            indicator=payload.indicator,
            operator=payload.operator,
            threshold=payload.threshold,
            registry=self.state_registry,
        )
        rule = AlertRule(
            user_id=user_id,
            security_id=security.id,
            enabled=payload.enabled,
            **fields,
        )
        self.session.add(rule)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("ALERT_CREATE_FAILED", "Alert rule could not be created.", 409) from exc
        refreshed = await self.repository.get_owned(rule.id, user_id)
        if refreshed is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        return _rule_data(refreshed)

    async def update(
        self, user_id: int, rule_id: int, payload: AlertRuleUpdateRequest
    ) -> AlertRuleData:
        rule = await self.repository.get_owned(rule_id, user_id)
        if rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        updates = payload.model_dump(exclude_unset=True)
        old_security_id = rule.security_id
        old_signature = _rule_signature(rule)
        security_id = updates.get("security_id", rule.security_id)
        security = await self.session.get(Security, security_id)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        condition_type = updates.get("condition_type", rule.condition_type)
        if "state_id" in updates and "state_code" not in updates:
            state_id, state_code = updates["state_id"], None
        elif "state_code" in updates and "state_id" not in updates:
            state_id, state_code = None, updates["state_code"]
        else:
            state_id = updates.get("state_id", rule.state_code)
            state_code = updates.get("state_code", rule.state_code)
        indicator = updates.get("indicator") if "indicator" in updates else rule.indicator_type
        operator = updates.get("operator") if "operator" in updates else rule.operator
        threshold = updates.get("threshold") if "threshold" in updates else rule.threshold
        fields = _condition_fields(
            condition_type=condition_type,
            state_id=state_id,
            state_code=state_code,
            indicator=indicator,
            operator=operator,
            threshold=threshold,
            registry=self.state_registry,
        )
        rule.security_id = security.id
        rule.condition_type = condition_type
        for key, value in fields.items():
            setattr(rule, key, value)
        if "enabled" in updates:
            rule.enabled = bool(updates["enabled"])
        new_signature = _rule_signature(rule)
        if old_security_id != security.id:
            instance = await self.repository.get_instance(rule.id, old_security_id)
            if instance is not None:
                await self.session.delete(instance)
        elif new_signature != old_signature:
            instance = await self.repository.get_instance(rule.id, old_security_id)
            if instance is not None:
                instance.status = AlertInstanceStatus.RESET
                instance.last_trigger_time = None
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("ALERT_UPDATE_FAILED", "Alert rule could not be updated.", 409) from exc
        refreshed = await self.repository.get_owned(rule.id, user_id)
        if refreshed is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        return _rule_data(refreshed)

    async def delete(self, user_id: int, rule_id: int) -> None:
        rule = await self.repository.get_owned(rule_id, user_id)
        if rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        await self.session.delete(rule)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("ALERT_DELETE_FAILED", "Alert rule could not be deleted.", 409) from exc

    async def notifications(self, user_id: int, *, limit: int = 100) -> list[NotificationData]:
        records = await self.notification_repository.list_for_user(user_id, limit=limit)
        return [_notification_data(item) for item in records]

    async def retry_notification(
        self,
        user_id: int,
        notification_id: int,
        *,
        provider: NotificationProvider | None = None,
    ) -> Notification:
        """Retry a failed notification while preserving its alert edge."""

        return await self.notification_service.retry(
            user_id=user_id,
            notification_id=notification_id,
            provider=provider,
        )

    async def evaluate_user(
        self,
        user_id: int,
        *,
        provider: NotificationProvider | None = None,
    ) -> list[AlertEvaluationResult]:
        rules = await self.repository.list_for_user(user_id)
        return [await self._evaluate_rule(rule, provider=provider) for rule in rules]

    async def evaluate_all(
        self,
        *,
        provider: NotificationProvider | None = None,
    ) -> list[AlertEvaluationResult]:
        """Evaluate every persisted rule; this is the scheduler integration seam."""

        statement = (
            select(AlertRule)
            .options(joinedload(AlertRule.security))
            .order_by(AlertRule.user_id.asc(), AlertRule.id.asc())
        )
        rules = list((await self.session.execute(statement)).scalars())
        return [await self._evaluate_rule(rule, provider=provider) for rule in rules]

    async def evaluate_rule(
        self,
        user_id: int,
        rule_id: int,
        *,
        provider: NotificationProvider | None = None,
    ) -> AlertEvaluationResult:
        rule = await self.repository.get_owned(rule_id, user_id)
        if rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        return await self._evaluate_rule(rule, provider=provider)

    async def _evaluate_rule(
        self,
        rule: AlertRule,
        *,
        provider: NotificationProvider | None,
    ) -> AlertEvaluationResult:
        # ``AlertInstance`` is intentionally scoped through its parent rule.
        # Locking that parent makes the read/evaluate/claim sequence atomic for
        # concurrent scheduler workers, including the first evaluation before
        # an instance row has been inserted.
        locked_rule = await self.repository.get_for_evaluation(rule.id)
        if locked_rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        rule = locked_rule
        _validate_rule_state(rule, self.state_registry)
        snapshots = await self.repository.latest_snapshots(rule.security_id)
        states = await self.repository.latest_states(rule.security_id)
        observation, context = _observation_for_rule(rule, snapshots, states)
        domain_rule = _domain_rule(rule, registry=self.state_registry)
        instance = await self.repository.get_instance(rule.id, rule.security_id)
        prior_status = instance.status if instance is not None else AlertInstanceStatus.RESET
        evaluation = self.evaluator.evaluate(
            domain_rule,
            observation,
            previous_state=prior_status,
        )
        now = datetime.now(UTC)
        if instance is None:
            instance = AlertInstance(
                alert_rule_id=rule.id,
                security_id=rule.security_id,
                status=evaluation.status.value,
                last_trigger_time=now if evaluation.triggered else None,
            )
            self.session.add(instance)
        else:
            instance.status = evaluation.status.value
            if evaluation.triggered:
                instance.last_trigger_time = now
        # Commit the edge state before making an external request.  If a worker
        # restarts after the provider accepts the request, the next pass still
        # sees ACTIVE and cannot emit a duplicate notification.
        await self.session.commit()
        if not evaluation.triggered:
            return AlertEvaluationResult(rule.id, evaluation.status.value, False)
        message = _notification_message(rule, context)
        notification = await self.notification_service.deliver(
            user_id=rule.user_id,
            alert_rule_id=rule.id,
            security_id=rule.security_id,
            title=_notification_title(rule, context),
            message=message,
            provider=provider,
            created_at=now,
        )
        return AlertEvaluationResult(rule.id, evaluation.status.value, True, notification.id)


# A descriptive alias makes the scheduler seam discoverable without creating
# a second service with subtly different edge-state semantics.
AlertEvaluationService = AlertService


async def evaluate_all_alerts(
    session: AsyncSession,
    *,
    provider: NotificationProvider | None = None,
) -> list[AlertEvaluationResult]:
    """Stable scheduler entry point for one batch evaluation.

    The scheduler owns session lifecycle and may inject a shared provider;
    business rules and delivery persistence remain inside the services.
    """

    return await AlertService(session).evaluate_all(provider=provider)


def _condition_fields(
    *,
    condition_type: str,
    state_id: str | None,
    state_code: str | None,
    indicator: str | None,
    operator: str | None,
    threshold: float | None,
    registry: StateRegistry = DEFAULT_REGISTRY,
) -> dict[str, Any]:
    kind = condition_type.strip().upper() if isinstance(condition_type, str) else condition_type
    if kind == "STATE":
        if (
            state_id is not None
            and state_code is not None
            and state_id.strip().upper() != state_code.strip().upper()
        ):
            raise ApiError("INVALID_ALERT_RULE", "State condition fields do not match.", 400)
        resolved = state_id or state_code
        if not resolved:
            raise ApiError("INVALID_ALERT_RULE", "State condition is required.", 400)
        resolved = _validated_state_code(registry, resolved)
        return {
            "condition_type": "STATE",
            "indicator_type": None,
            "state_code": resolved.strip().upper(),
            "operator": None,
            "threshold": None,
        }
    if kind != "VALUE":
        raise ApiError("INVALID_ALERT_RULE", "Condition type must be VALUE or STATE.", 400)
    if not indicator or operator is None or threshold is None:
        raise ApiError(
            "INVALID_ALERT_RULE",
            "Value condition requires indicator, operator, and threshold.",
            400,
        )
    normalized_indicator = indicator.strip().upper()
    normalized_operator = operator.strip()
    domain_operator = "==" if normalized_operator == "=" else normalized_operator
    try:
        ValueCondition(normalized_indicator, domain_operator, threshold)
    except ValueError as exc:
        raise ApiError("INVALID_ALERT_RULE", "Value condition is invalid.", 400) from exc
    return {
        "condition_type": "VALUE",
        "indicator_type": normalized_indicator,
        "state_code": None,
        "operator": normalized_operator,
        "threshold": float(threshold),
    }


def _domain_rule(
    rule: AlertRule,
    *,
    registry: StateRegistry = DEFAULT_REGISTRY,
) -> DomainAlertRule:
    if rule.condition_type == "STATE":
        return DomainAlertRule(
            condition=StateCondition(_validated_state_code(registry, rule.state_code)),
            security_id=rule.security_id,
            enabled=bool(rule.enabled),
            rule_id=rule.id,
        )
    operator = "==" if rule.operator == "=" else str(rule.operator)
    return DomainAlertRule(
        condition=ValueCondition(rule.indicator_type or "", operator, float(rule.threshold)),
        security_id=rule.security_id,
        enabled=bool(rule.enabled),
        rule_id=rule.id,
    )


def _validate_rule_state(rule: AlertRule, registry: StateRegistry) -> None:
    if rule.condition_type == "STATE":
        _validated_state_code(registry, rule.state_code)


def _validated_state_code(registry: StateRegistry, state_code: str | None) -> str:
    try:
        return registry.get(state_code or "").code
    except (UnknownStateError, TypeError, ValueError) as exc:
        raise ApiError("INVALID_ALERT_RULE", "State condition is invalid.", 400) from exc


def _rule_signature(rule: AlertRule) -> tuple[Any, ...]:
    return (
        rule.security_id,
        rule.condition_type,
        rule.indicator_type,
        rule.state_code,
        rule.operator,
        float(rule.threshold) if rule.threshold is not None else None,
        bool(rule.enabled),
    )


def _rule_data(rule: AlertRule) -> AlertRuleData:
    security = rule.security
    return AlertRuleData(
        id=rule.id,
        security_id=rule.security_id,
        condition_type=rule.condition_type,
        state_id=rule.state_code if rule.condition_type == "STATE" else None,
        state_code=rule.state_code if rule.condition_type == "STATE" else None,
        indicator=rule.indicator_type if rule.condition_type == "VALUE" else None,
        indicator_type=rule.indicator_type if rule.condition_type == "VALUE" else None,
        operator=rule.operator if rule.condition_type == "VALUE" else None,
        threshold=float(rule.threshold) if rule.threshold is not None else None,
        enabled=bool(rule.enabled),
        symbol=security.symbol if security is not None else None,
        name=security.name if security is not None else None,
        security=_security_data(security) if security is not None else None,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _notification_data(notification: Notification) -> NotificationData:
    content = dict(notification.content or {})
    state_id = content.get("state")
    if not isinstance(state_id, str):
        state_id = None
    indicator = content.get("indicator")
    if not isinstance(indicator, str):
        indicator = None
    security = notification.security
    return NotificationData(
        id=notification.id,
        alert_rule_id=notification.alert_rule_id,
        security_id=notification.security_id,
        symbol=security.symbol if security is not None else str(notification.security_id or ""),
        name=security.name if security is not None else None,
        title=notification.title,
        channel=notification.channel,
        status=notification.status,
        content=content,
        indicator=indicator,
        state_id=state_id,
        created_at=notification.created_at,
        sent_at=notification.sent_at,
    )


def _security_data(security: Security) -> SecurityData:
    return SecurityData(
        id=security.id,
        security_id=security.id,
        symbol=security.symbol,
        name=security.name,
        market=security.market,
        exchange=security.exchange,
        security_type=security.security_type,
        type=security.security_type,
        status=security.status,
    )


def _observation_for_rule(
    rule: AlertRule,
    snapshots: Sequence[IndicatorSnapshot],
    states: Sequence[IndicatorState],
) -> tuple[dict[str, Any], dict[str, Any]]:
    values: dict[str, Any] = {}
    selected_snapshot: _SnapshotObservation | None = None
    rule_indicator = _normalize_rule_indicator(rule.indicator_type)
    for snapshot in snapshots:
        try:
            variant = select_snapshot_variant(snapshot, snapshot.indicator_type)
        except ApiError:
            continue
        if variant is None:
            continue
        numeric_values = {
            str(key): float(value)
            for key, value in (variant.values or {}).items()
            if _is_number(value)
        }
        values.update(numeric_values)
        if "value" in numeric_values:
            values[normalize_indicator_type(snapshot.indicator_type)] = numeric_values["value"]
        elif len(numeric_values) == 1:
            values[normalize_indicator_type(snapshot.indicator_type)] = next(
                iter(numeric_values.values())
            )
        if rule_indicator == normalize_indicator_type(snapshot.indicator_type):
            selected_snapshot = _snapshot_observation(snapshot, variant)
    active_states = {
        item.state_code: item.status == AlertInstanceStatus.ACTIVE.value for item in states
    }
    state_row = next((item for item in states if item.state_code == rule.state_code), None)
    context: dict[str, Any] = {
        "date": _context_date(selected_snapshot, state_row),
        "snapshot": selected_snapshot,
        "state": state_row,
        "values": values,
        "active_states": active_states,
    }
    observation = {"values": values, "states": active_states}
    return observation, context


def _normalize_rule_indicator(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_indicator_type(value)
    except ApiError:
        return value.strip().upper()


def _snapshot_observation(snapshot: IndicatorSnapshot, variant: Any) -> _SnapshotObservation:
    return _SnapshotObservation(
        trade_date=snapshot.trade_date,
        indicator_type=normalize_indicator_type(snapshot.indicator_type),
        parameters=dict(variant.parameters),
        values=dict(variant.values),
        previous_values=(
            dict(variant.previous_values) if variant.previous_values is not None else None
        ),
        delta=dict(variant.delta) if variant.delta is not None else None,
    )


def _context_date(snapshot: IndicatorSnapshot | None, state: IndicatorState | None) -> date:
    if snapshot is not None:
        return snapshot.trade_date
    if state is not None:
        return state.trade_date
    return datetime.now(UTC).date()


def _notification_message(rule: AlertRule, context: Mapping[str, Any]) -> NotificationMessage:
    snapshot = context.get("snapshot")
    state = context.get("state")
    security = rule.security
    indicator = (
        rule.indicator_type
        if rule.condition_type == "VALUE"
        else getattr(state, "indicator_type", None) or "STATE"
    )
    current = _extract_value(snapshot.values if snapshot is not None else None)
    previous = _extract_value(snapshot.previous_values if snapshot is not None else None)
    change = _extract_value(snapshot.delta if snapshot is not None else None)
    return NotificationMessage(
        stock_name=security.name if security is not None else str(rule.security_id),
        stock_code=security.symbol if security is not None else str(rule.security_id),
        indicator=indicator,
        state=rule.state_code if rule.condition_type == "STATE" else None,
        current_value=current,
        previous_value=previous,
        change=change,
        date=context["date"],
    )


def _notification_title(rule: AlertRule, context: Mapping[str, Any]) -> str:
    if rule.condition_type == "STATE":
        state = context.get("state")
        metadata = getattr(state, "metadata_json", None) or {}
        title = metadata.get("name") if isinstance(metadata, dict) else None
        return str(title or rule.state_code or "状态触发")
    threshold = float(rule.threshold)
    return f"{rule.indicator_type} {rule.operator} {threshold:g}"


def _extract_value(values: Mapping[str, Any] | None) -> float | None:
    if not isinstance(values, Mapping):
        return None
    if _is_number(values.get("value")):
        return float(values["value"])
    numeric = [float(value) for value in values.values() if _is_number(value)]
    return numeric[0] if len(numeric) == 1 else None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    return math.isfinite(float(value))


__all__ = [
    "AlertEvaluationResult",
    "AlertEvaluationService",
    "AlertService",
    "evaluate_all_alerts",
]
