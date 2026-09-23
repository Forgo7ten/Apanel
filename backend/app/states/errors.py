"""Domain errors for the pure indicator state engine."""

from __future__ import annotations


class StateError(ValueError):
    """Base class for state definition, snapshot, and evaluation errors."""


class InvalidStateDefinitionError(StateError):
    """Raised when a state definition does not satisfy the registry contract."""


class UnknownStateError(StateError):
    """Raised when a registry lookup has no state with the requested code."""


class InvalidSnapshotError(StateError):
    """Raised when an indicator snapshot cannot be normalized."""


class MissingStateValues(StateError):
    """Internal signal used by evaluators when a snapshot field is absent."""

    def __init__(self, reason: str = "missing_values") -> None:
        self.reason = reason
        super().__init__(reason)
