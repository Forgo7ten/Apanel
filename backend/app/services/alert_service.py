"""Application services for alert CRUD, Edge Trigger evaluation and delivery."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
    canonicalize_state_parameters,
    normalize_indicator_type,
    parameter_key,
)
from app.models import (
    AlertInstance,
    AlertInstanceStatus,
    AlertRule,
    Notification,
    Security,
    User,
    UserStatus,
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
from app.services.analysis_control import ensure_monitor_capacity, lock_account, mark_demand_changed
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AlertEvaluationResult:
    """Small scheduler-facing result for one evaluated rule."""

    rule_id: int
    status: str
    triggered: bool
    notification_id: int | None = None
    error_code: str | None = None
    notification_ids: tuple[int, ...] = ()


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
        self.observation_service = AlertObservationService(
            session, state_registry=self.state_registry
        )
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
        await lock_account(self.session, user_id)
        if payload.enabled:
            await ensure_monitor_capacity(self.session, user_id, payload.security_id)
        security = await self.session.get(Security, payload.security_id)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        if security.status != "ACTIVE":
            raise ApiError("SECURITY_INACTIVE", "Security is not active.", 409)
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
            if rule.enabled:
                await mark_demand_changed(self.session, [rule.security_id])
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
        await lock_account(self.session, user_id)
        rule = await self.repository.get_for_evaluation(rule_id, user_id=user_id)
        if rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        updates = payload.model_dump(exclude_unset=True)
        old_security_id = rule.security_id
        old_signature = _rule_signature(rule)
        security_id = updates.get("security_id", rule.security_id)
        security = await self.session.get(Security, security_id)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        if security.status != "ACTIVE":
            raise ApiError("SECURITY_INACTIVE", "Security is not active.", 409)
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
        if updates.get("enabled", rule.enabled):
            await ensure_monitor_capacity(
                self.session, user_id, security_id, exclude_rule_id=rule.id
            )
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
        if new_signature != old_signature:
            rule.rule_version += 1
        if old_security_id != security.id:
            instance = await self.repository.get_instance(rule.id, old_security_id)
            if instance is not None:
                await self.session.delete(instance)
        elif new_signature != old_signature:
            instance = await self.repository.get_instance(rule.id, old_security_id)
            if instance is not None:
                instance.status = AlertInstanceStatus.RESET
                instance.last_trigger_time = None
                instance.rule_version = rule.rule_version
                instance.last_evaluated_trade_date = None
        try:
            if new_signature != old_signature:
                await mark_demand_changed(self.session, [old_security_id, security.id])
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
        await lock_account(self.session, user_id)
        rule = await self.repository.get_for_evaluation(rule_id, user_id=user_id)
        if rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        await mark_demand_changed(self.session, [rule.security_id])
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
            await self._evaluate_rule(rule_id, provider=provider, dispatch_after_commit=True)
            for rule_id in [rule.id for rule in rules]
        ]

    async def evaluate_all(
        self,
        *,
        provider: NotificationProvider | None = None,
        observation_date: date | None = None,
        dividend_unavailable_symbols: set[str] | frozenset[str] | None = None,
        eligible_streams: tuple[tuple[str, str], ...] | None = None,
    ) -> list[AlertEvaluationResult]:
        """Evaluate every persisted rule; this is the scheduler integration seam."""

        statement = (
            select(AlertRule.id, AlertRule.indicator_type, Security.symbol, AlertRule.adjust_type)
            .join(Security, Security.id == AlertRule.security_id)
            .join(User, User.id == AlertRule.user_id)
            .where(
                AlertRule.enabled.is_(True),
                User.status == UserStatus.ACTIVE,
                Security.status == "ACTIVE",
            )
            .order_by(AlertRule.user_id.asc(), AlertRule.id.asc())
        )
        targets = (await self.session.execute(statement)).all()
        blocked = {symbol.upper() for symbol in (dividend_unavailable_symbols or set())}
        results = []
        eligible = None if eligible_streams is None else set(eligible_streams)
        for rule_id, indicator, symbol, adjustment in targets:
            if (
                str(indicator or "").upper() != "DIVIDEND_YIELD"
                and eligible is not None
                and (symbol, adjustment) not in eligible
            ):
                continue
            if str(indicator or "").upper() == "DIVIDEND_YIELD" and symbol.upper() in blocked:
                continue
            try:
                results.append(
                    await self._evaluate_rule(
                        int(rule_id), provider=provider, observation_date=observation_date
                    )
                )
            except ApiError as error:
                await self.session.rollback()
                logger.warning(
                    "alert_evaluation_skipped",
                    extra={
                        "rule_id": rule_id,
                        "error_code": error.code,
                    },
                )
                results.append(
                    AlertEvaluationResult(
                        int(rule_id), AlertInstanceStatus.RESET.value, False, error_code=error.code
                    )
                )
        return results

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
        rule: AlertRule | int,
        *,
        provider: NotificationProvider | None,
        observation_date: date | None = None,
        dispatch_after_commit: bool = False,
    ) -> AlertEvaluationResult:
        rule_id = rule if isinstance(rule, int) else rule.id
        locked_rule = await self.repository.get_for_evaluation(rule_id)
        if locked_rule is None:
            raise ApiError("ALERT_NOT_FOUND", "Alert rule was not found.", 404)
        rule = locked_rule
        if not rule.enabled:
            await self.session.commit()
            return AlertEvaluationResult(rule.id, AlertInstanceStatus.RESET.value, False)
        _validate_rule_state(rule, self.state_registry)

        context: dict[str, Any]
        if rule.condition_type == "VALUE":
            resolved = await self.observation_service.resolve_value(rule)
            if resolved is None or (
                observation_date is not None and resolved.observation_date != observation_date
            ):
                return await self._unavailable_observation(rule, observation_date)
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
                return await self._unavailable_observation(rule, observation_date)
            observation = resolved_state.active
            context = {
                "date": resolved_state.observation_date,
                "current_value": None,
                "previous_value": None,
                "change": None,
                "value_details": resolved_state.value_context,
                "snapshot": None,
                "state": resolved_state.state,
            }

        domain_rule = _domain_rule(rule, registry=self.state_registry)
        instance = await self.repository.get_instance(rule.id, rule.security_id)
        if instance is not None and instance.rule_version != rule.rule_version:
            instance.status = AlertInstanceStatus.RESET
            instance.last_evaluated_trade_date = None
            instance.rule_version = rule.rule_version
            instance.last_consumed_state_seq = 0
        if (
            observation_date is not None
            and instance is not None
            and instance.last_evaluated_trade_date is not None
            and context["date"] < instance.last_evaluated_trade_date
        ):
            # Scheduled corrections/replays are not new real-time observations.
            await self.session.commit()
            return AlertEvaluationResult(rule.id, instance.status, False)
        if (
            observation_date is not None
            and instance is not None
            and instance.last_evaluated_trade_date is not None
            and instance.last_evaluated_trade_date < context["date"]
            and rule.indicator_type != "DIVIDEND_YIELD"
        ):
            from app.services.alert_replay import catch_up

            replay = await catch_up(self, rule, instance, context, observation)
            if replay is not None:
                return replay
        prior_status = instance.status if instance is not None else AlertInstanceStatus.RESET
        evaluation = self.evaluator.evaluate(domain_rule, observation, previous_state=prior_status)
        if (
            observation_date is not None
            and instance is not None
            and instance.last_evaluated_trade_date == context["date"]
        ):
            # A correction updates the baseline but is not a fresh notification.
            instance.status = evaluation.status.value
            await self.session.commit()
            return AlertEvaluationResult(rule.id, evaluation.status.value, False)
        now = datetime.now(UTC)
        if instance is None:
            instance = AlertInstance(
                alert_rule_id=rule.id,
                security_id=rule.security_id,
                status=evaluation.status.value,
                last_trigger_time=now if evaluation.triggered else None,
                trigger_sequence=rule.trigger_sequence,
                rule_version=rule.rule_version,
                last_evaluated_trade_date=context["date"],
            )
            self.session.add(instance)
        else:
            instance.status = evaluation.status.value
            if evaluation.triggered:
                instance.last_trigger_time = now
        instance.last_evaluated_trade_date = context["date"]
        if rule.condition_type == "STATE":
            from app.models import AnalysisStream

            stream = await self.session.get(AnalysisStream, (rule.security_id, rule.adjust_type))
            if stream is not None:
                instance.last_consumed_state_seq = stream.event_sequence
        if not evaluation.triggered:
            await self.session.commit()
            return AlertEvaluationResult(rule.id, evaluation.status.value, False)

        notification = await self._enqueue_trigger(rule, instance, context)
        # Edge state and PENDING outbox row become durable atomically.
        await self.session.commit()
        if dispatch_after_commit:
            await self.notification_service.dispatch(notification.id, provider=provider)
        return AlertEvaluationResult(rule.id, evaluation.status.value, True, notification.id)

    async def _unavailable_observation(self, rule, observation_date):
        from app.services.alert_replay import catch_up
        from app.services.analysis_control import publication_is_current, published_stream_for_read

        if observation_date is None:
            await self.session.commit()
            return AlertEvaluationResult(rule.id, AlertInstanceStatus.RESET.value, False)
        stream = await published_stream_for_read(self.session, rule.security_id, rule.adjust_type)
        # Do not consume the day while its publication is still pending. Once
        # publication is complete, UNKNOWN is a checked observation, not a reset.
        if (
            stream is None
            or not publication_is_current(stream)
            or stream.state_through != observation_date
        ):
            await self.session.commit()
            return AlertEvaluationResult(rule.id, AlertInstanceStatus.RESET.value, False)
        instance = await self.repository.get_instance(rule.id, rule.security_id)
        if instance is not None and instance.rule_version == rule.rule_version:
            if (
                instance.last_evaluated_trade_date is not None
                and instance.last_evaluated_trade_date < observation_date
            ):
                context = {"date": observation_date, "snapshot": None, "state": None}
                result = await catch_up(self, rule, instance, context, None)
                if result is not None:
                    return result
        if instance is None:
            instance = AlertInstance(
                alert_rule_id=rule.id,
                security_id=rule.security_id,
                status=AlertInstanceStatus.RESET,
                trigger_sequence=rule.trigger_sequence,
                rule_version=rule.rule_version,
                last_evaluated_trade_date=observation_date,
                last_consumed_state_seq=stream.event_sequence,
            )
            self.session.add(instance)
        elif instance.rule_version != rule.rule_version:
            instance.rule_version = rule.rule_version
            instance.status = AlertInstanceStatus.RESET
            instance.last_evaluated_trade_date = observation_date
            instance.last_consumed_state_seq = stream.event_sequence
        elif (
            instance.last_evaluated_trade_date is None
            or instance.last_evaluated_trade_date <= observation_date
        ):
            instance.last_evaluated_trade_date = observation_date
            instance.last_consumed_state_seq = stream.event_sequence
        await self.session.commit()
        return AlertEvaluationResult(rule.id, instance.status, False)

    async def _enqueue_trigger(self, rule, instance, context):
        """All trigger identities are owned by the locked rule, including catch-up."""
        now = datetime.now(UTC)
        rule.trigger_sequence = max(rule.trigger_sequence, instance.trigger_sequence) + 1
        instance.trigger_sequence = rule.trigger_sequence
        instance.last_trigger_time = now
        notification = await self.notification_service.enqueue(
            user_id=rule.user_id,
            alert_rule_id=rule.id,
            security_id=rule.security_id,
            title=_notification_title(rule, context),
            message=_notification_message(rule, context),
            trigger_sequence=instance.trigger_sequence,
            rule_version=rule.rule_version,
            observation_date=context["date"],
            created_at=now,
        )
        provenance = {
            key: context[key]
            for key in ("source_event_id", "source_revision", "catch_up")
            if key in context
        }
        if provenance:
            notification.content = {**notification.content, **provenance}
        return notification


# A descriptive alias makes the scheduler seam discoverable without creating
# a second service with subtly different edge-state semantics.
AlertEvaluationService = AlertService


async def evaluate_all_alerts(
    session: AsyncSession,
    *,
    provider: NotificationProvider | None = None,
    observation_date: date | None = None,
    dividend_unavailable_symbols: set[str] | frozenset[str] | None = None,
    eligible_streams: tuple[tuple[str, str], ...] | None = None,
) -> list[AlertEvaluationResult]:
    """Stable scheduler entry point for one batch evaluation.

    The scheduler owns session lifecycle and may inject a shared provider;
    business rules and delivery persistence remain inside the services.
    """

    return await AlertService(session).evaluate_all(
        provider=provider,
        observation_date=observation_date,
        dividend_unavailable_symbols=dividend_unavailable_symbols,
        eligible_streams=eligible_streams,
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
        normalized_parameters = canonicalize_state_parameters(definition.indicator_type, parameters)
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
        rule_version=rule.rule_version,
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
        value_details=context.get("value_details"),
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
