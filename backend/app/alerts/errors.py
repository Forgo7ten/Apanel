"""Errors raised by the alert domain."""

from __future__ import annotations


class AlertDomainError(ValueError):
    """Base class for invalid alert rules and observations."""


class InvalidAlertRuleError(AlertDomainError):
    """An alert rule is missing or contains an invalid condition."""


class InvalidAlertObservationError(AlertDomainError):
    """An observation cannot be normalized for the requested condition."""
