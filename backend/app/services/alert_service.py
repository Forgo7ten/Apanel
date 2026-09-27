"""Application services for alert CRUD, Edge Trigger evaluation and delivery."""

from __future__ import annotations

import math
from collections.abc import Mapping
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
from app.indicators.metadata import canonical_indicator_field
from app.indicators.parameters import (
    canonicalize_parameters,
    normalize_indicator_type,
    parameter_key,
)
from app.models import (
    AlertInstance,
    AlertInstanceStatus,
    AlertRule,
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
from app.services.alert_observation_service import AlertObservationService
from app.services.bootstrap_scheduler import (
    NoopSecurityBootstrapScheduler,
    SecurityBootstrapScheduler,
)
from app.services.notification_service import (
    NotificationService,
    ProviderFactory,
    is_notification_retryable,
)
from app.services.state_service import state_parameter_key
from app.states import DEFAULT_REGISTRY, StateRegistry, UnknownStateError


@dataclass(frozen=True, slots=True)
class AlertEvaluationResult:
    """Small scheduler-facing result for one evaluated rule."""

    rule_id: int
    status: str
    triggered: bool
    notification_id: int | None = None


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
        bootstrap_scheduler: SecurityBootstrapScheduler | None = None,
    ) -> None:
        self.session = session
        self.bootstrap_scheduler = bootstrap_scheduler or NoopSecurityBootstrapScheduler()
        self.repository = AlertRepository(session)
        self.notification_repository = NotificationRepository(session)
        self.state_registry = state_registry or DEFAULT_REGISTRY
        self.observation_service = AlertObservationService(session)
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
            parameters=payload.parameters,
            field=payload.field,
            adjust_type=payload.adjust_type,
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
        if refreshed.enabled and refreshed.security is not None:
            self.bootstrap_scheduler.enqueue(refreshed.security.symbol)
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
        parameters = updates.get("parameters") if "parameters" in updates else rule.parameters
        field = updates.get("field") if "field" in updates else rule.field
        adjust_type = updates.get("adjust_type") if "adjust_type" in updates else rule.adjust_type
        fields = _condition_fields(
            condition_type=condition_type,
            state_id=state_id,
            state_code=state_code,
            indicator=indicator,
            operator=operator,
            threshold=threshold,
            parameters=parameters,
            field=field,
            adjust_type=adjust_type,
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
        if refreshed.enabled and refreshed.security is not None:
            self.bootstrap_scheduler.enqueue(refreshed.security.symbol)
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
    ) -> NotificationData:
        """Retry a failed notification while preserving its alert edge."""

        notification = await self.notification_service.retry(
            user_id=user_id,
            notification_id=notification_id,
            provider=provider,
        )
        return _notification_data(notification)

    async def evaluate_user(
        self,
        user_id: int,
        *,
        provider: NotificationProvider | None = None,
    ) -> list[AlertEvaluationResult]:
        rules = await self.repository.list_for_user(user_id)
        return [
            await self._evaluate_rule(rule, provider=provider, dispatch_after_commit=True)
            for rule in rules
        ]

    async def evaluate_all(
        self,
        *,
        provider: NotificationProvider | None = None,
        observation_date: date | None = None,
        dividend_unavailable_symbols: set[str] | frozenset[str] | None = None,
    ) -> list[AlertEvaluationResult]:
        """Evaluate every persisted rule; this is the scheduler integration seam."""

        statement = (
            select(AlertRule)
            .options(joinedload(AlertRule.security))
            .order_by(AlertRule.user_id.asc(), AlertRule.id.asc())
        )
        rules = list((await self.session.execute(statement)).scalars())
        blocked = {symbol.upper() for symbol in (dividend_unavailable_symbols or set())}
        return [
            await self._evaluate_rule(rule, provider=provider, observation_date=observation_date)
            for rule in rules
            if not (
                str(rule.indicator_type or "").upper() == "DIVIDEND_YIELD"
                and rule.security is not None
                and rule.security.symbol.upper() in blocked
            )
        ]

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
        return await self._evaluate_rule(rule, provider=provider, dispatch_after_commit=True)

    async def _evaluate_rule(
        self,
        rule: AlertRule,
        *,
        provider: NotificationProvider | None,
        observation_date: date | None = None,
        dispatch_after_commit: bool = False,
    ) -> AlertEvaluationResult:
        locked_rule = await self.repository.get_for_evaluation(rule.id)
        if locked_rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        rule = locked_rule
        if not rule.enabled:
            return AlertEvaluationResult(rule.id, AlertInstanceStatus.RESET.value, False)
        _validate_rule_state(rule, self.state_registry)
        _ensure_rule_target_identity(rule, self.state_registry)

        context: dict[str, Any]
        if rule.condition_type == "VALUE":
            resolved = await self.observation_service.resolve_value(rule)
            if resolved is None or (
                observation_date is not None and resolved.observation_date != observation_date
            ):
                return AlertEvaluationResult(rule.id, AlertInstanceStatus.RESET.value, False)
            observation = {"value": resolved.value}
            context = {
                "date": resolved.observation_date,
                "current_value": resolved.value,
                "previous_value": resolved.previous_value,
                "change": resolved.delta,
                "snapshot": resolved.snapshot,
                "state": None,
            }
        else:
            resolved_state = await self.observation_service.resolve_state(rule)
            if resolved_state is None or (
                observation_date is not None and resolved_state.observation_date != observation_date
            ):
                return AlertEvaluationResult(rule.id, AlertInstanceStatus.RESET.value, False)
            observation = resolved_state.active
            context = {
                "date": resolved_state.observation_date,
                "current_value": None,
                "previous_value": None,
                "change": None,
                "snapshot": None,
                "state": resolved_state.state,
            }

        domain_rule = _domain_rule(rule, registry=self.state_registry)
        instance = await self.repository.get_instance(rule.id, rule.security_id)
        prior_status = instance.status if instance is not None else AlertInstanceStatus.RESET
        evaluation = self.evaluator.evaluate(domain_rule, observation, previous_state=prior_status)
        now = datetime.now(UTC)
        if instance is None:
            instance = AlertInstance(
                alert_rule_id=rule.id,
                security_id=rule.security_id,
                status=evaluation.status.value,
                last_trigger_time=now if evaluation.triggered else None,
                trigger_sequence=1 if evaluation.triggered else 0,
            )
            self.session.add(instance)
        else:
            instance.status = evaluation.status.value
            if evaluation.triggered:
                instance.last_trigger_time = now
                instance.trigger_sequence += 1
        if not evaluation.triggered:
            await self.session.commit()
            return AlertEvaluationResult(rule.id, evaluation.status.value, False)

        message = _notification_message(rule, context)
        notification = await self.notification_service.enqueue(
            user_id=rule.user_id,
            alert_rule_id=rule.id,
            security_id=rule.security_id,
            title=_notification_title(rule, context),
            message=message,
            trigger_sequence=instance.trigger_sequence,
            observation_date=context["date"],
            created_at=now,
        )
        # Edge state and PENDING outbox row become durable atomically.
        await self.session.commit()
        if dispatch_after_commit:
            await self.notification_service.dispatch(notification.id, provider=provider)
        return AlertEvaluationResult(rule.id, evaluation.status.value, True, notification.id)


# A descriptive alias makes the scheduler seam discoverable without creating
# a second service with subtly different edge-state semantics.
AlertEvaluationService = AlertService


async def evaluate_all_alerts(
    session: AsyncSession,
    *,
    provider: NotificationProvider | None = None,
    observation_date: date | None = None,
    dividend_unavailable_symbols: set[str] | frozenset[str] | None = None,
) -> list[AlertEvaluationResult]:
    """Stable scheduler entry point for one batch evaluation.

    The scheduler owns session lifecycle and may inject a shared provider;
    business rules and delivery persistence remain inside the services.
    """

    return await AlertService(session).evaluate_all(
        provider=provider,
        observation_date=observation_date,
        dividend_unavailable_symbols=dividend_unavailable_symbols,
    )


def _condition_fields(
    *,
    condition_type: str,
    state_id: str | None,
    state_code: str | None,
    indicator: str | None,
    operator: str | None,
    threshold: float | None,
    parameters: Mapping[str, Any] | None,
    field: str | None,
    adjust_type: str | None,
    registry: StateRegistry = DEFAULT_REGISTRY,
) -> dict[str, Any]:
    kind = condition_type.strip().upper() if isinstance(condition_type, str) else condition_type
    selected_adjustment = str(adjust_type or "qfq").strip().lower()
    if selected_adjustment not in {"qfq", "none"}:
        raise ApiError("INVALID_ALERT_RULE", "Adjustment must be qfq or none.", 400)
    if kind == "STATE":
        if (
            state_id is not None
            and state_code is not None
            and state_id.strip().upper() != state_code.strip().upper()
        ):
            raise ApiError("INVALID_ALERT_RULE", "State condition fields do not match.", 400)
        resolved = _validated_state_code(registry, state_id or state_code)
        definition = registry.get(resolved)
        if definition.indicator_type == "MA":
            raw = dict(parameters or {"short_period": 5, "long_period": 10})
            try:
                short = int(raw.get("short_period", raw.get("short", 5)))
                long = int(raw.get("long_period", raw.get("long", 10)))
            except (TypeError, ValueError) as exc:
                raise ApiError("INVALID_ALERT_RULE", "MA state periods are invalid.", 400) from exc
            if short <= 0 or long <= 0 or short >= long:
                raise ApiError("INVALID_ALERT_RULE", "MA state periods are invalid.", 400)
            normalized_parameters = {"short_period": short, "long_period": long}
        else:
            normalized_parameters = canonicalize_parameters(
                definition.indicator_type,
                parameters,
                fill_defaults=True,
            )
        return {
            "condition_type": "STATE",
            "indicator_type": definition.indicator_type,
            "state_code": resolved,
            "operator": None,
            "threshold": None,
            "parameters": normalized_parameters,
            "parameter_key": state_parameter_key(resolved, normalized_parameters),
            "field": None,
            "adjust_type": selected_adjustment,
        }
    if kind != "VALUE" or not indicator or operator is None or threshold is None:
        raise ApiError(
            "INVALID_ALERT_RULE",
            "Value condition requires indicator, operator, and threshold.",
            400,
        )
    normalized_indicator = normalize_indicator_type(indicator)
    normalized_operator = operator.strip()
    domain_operator = "==" if normalized_operator == "=" else normalized_operator
    ValueCondition(normalized_indicator, domain_operator, threshold)
    if normalized_indicator == "DIVIDEND_YIELD":
        normalized_parameters: dict[str, Any] = {}
        selected_field = "value"
        selected_adjustment = "none"
        selected_key = parameter_key("DIVIDEND_YIELD", {})
    else:
        normalized_parameters = canonicalize_parameters(
            normalized_indicator, parameters, fill_defaults=True
        )
        selected_field = canonical_indicator_field(
            normalized_indicator, normalized_parameters, field
        )
        selected_key = parameter_key(normalized_indicator, normalized_parameters)
    return {
        "condition_type": "VALUE",
        "indicator_type": normalized_indicator,
        "state_code": None,
        "operator": normalized_operator,
        "threshold": float(threshold),
        "parameters": normalized_parameters,
        "parameter_key": selected_key,
        "field": selected_field,
        "adjust_type": selected_adjustment,
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


def _ensure_rule_target_identity(
    rule: AlertRule, registry: StateRegistry = DEFAULT_REGISTRY
) -> None:
    """Bridge legacy rules to precise targets without guessing composite fields."""

    if rule.condition_type == "STATE":
        if not rule.state_code:
            return
        definition = registry.get(rule.state_code)
        if rule.parameters:
            parameters = dict(rule.parameters)
        elif definition.indicator_type == "MA":
            parameters = {"short_period": 5, "long_period": 10}
        else:
            parameters = canonicalize_parameters(
                definition.indicator_type, None, fill_defaults=True
            )
        rule.indicator_type = definition.indicator_type
        rule.parameters = parameters
        rule.parameter_key = rule.parameter_key or state_parameter_key(rule.state_code, parameters)
        rule.adjust_type = rule.adjust_type or "qfq"
        return
    indicator = normalize_indicator_type(rule.indicator_type or "")
    if indicator == "DIVIDEND_YIELD":
        rule.parameters = {}
        rule.parameter_key = rule.parameter_key or parameter_key(indicator, {})
        rule.field = rule.field or "value"
        rule.adjust_type = "none"
        return
    if rule.parameter_key and rule.field:
        return
    if indicator not in {"RSI", "PROJECTED_MA"}:
        return
    parameters = canonicalize_parameters(indicator, rule.parameters, fill_defaults=True)
    rule.parameters = parameters
    rule.parameter_key = parameter_key(indicator, parameters)
    rule.field = canonical_indicator_field(indicator, parameters, rule.field)
    rule.adjust_type = rule.adjust_type or "qfq"


def _rule_signature(rule: AlertRule) -> tuple[Any, ...]:
    return (
        rule.security_id,
        rule.condition_type,
        rule.indicator_type,
        rule.state_code,
        rule.parameter_key,
        rule.field,
        rule.adjust_type,
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
        parameters=dict(rule.parameters) if isinstance(rule.parameters, dict) else None,
        parameter_key=rule.parameter_key,
        field=rule.field,
        adjust_type=rule.adjust_type,
        needs_review=bool(
            rule.condition_type == "VALUE" and (not rule.parameter_key or not rule.field)
        ),
        enabled=bool(rule.enabled),
        symbol=security.symbol if security is not None else None,
        name=security.name if security is not None else None,
        security=_security_data(security) if security is not None else None,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _notification_data(notification: Notification) -> NotificationData:
    content = _safe_notification_content(notification)
    state_id = content.get("state")
    if not isinstance(state_id, str):
        state_id = None
    indicator = content.get("indicator")
    if not isinstance(indicator, str):
        indicator = None
    security = notification.security
    error_code, error_message = _safe_notification_error(notification)
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
        error_code=error_code,
        error_message=error_message,
        retryable=is_notification_retryable(notification),
        indicator=indicator,
        state_id=state_id,
        created_at=notification.created_at,
        sent_at=notification.sent_at,
    )


def _safe_notification_error(notification: Notification) -> tuple[str | None, str | None]:
    """Project only bounded, provider-neutral delivery diagnostics."""

    raw_code = notification.error_code
    raw_message = notification.error_message
    if not raw_code and not raw_message:
        return None, None
    if (
        isinstance(raw_code, str)
        and raw_code
        and len(raw_code) <= 64
        and all(
            character.isupper() or character.isdigit() or character == "_" for character in raw_code
        )
    ):
        code = raw_code
    else:
        code = "PROVIDER_ERROR"
    message = (
        "Notification content cannot be retried."
        if code == "INVALID_NOTIFICATION_CONTENT"
        else "Notification provider failed."
    )
    return code, message


def _safe_notification_content(notification: Notification) -> dict[str, Any]:
    """Prevent legacy provider diagnostics nested in JSON from reaching clients."""

    content = dict(notification.content or {})
    if "error" not in content:
        return content
    error_code, error_message = _safe_notification_error(notification)
    if error_code is None or error_message is None:
        content.pop("error", None)
    else:
        content["error"] = {"code": error_code, "message": error_message}
    return content


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


def _notification_message(rule: AlertRule, context: Mapping[str, Any]) -> NotificationMessage:
    state = context.get("state")
    security = rule.security
    indicator = (
        rule.indicator_type
        if rule.condition_type == "VALUE"
        else getattr(state, "indicator_type", None) or "STATE"
    )
    current = context.get("current_value")
    previous = context.get("previous_value")
    change = context.get("change")
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
