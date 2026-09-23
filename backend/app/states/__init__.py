"""Pure, plugin-oriented indicator state recognition."""

from .definitions import (
    StateDefinition,
    StateEvaluator,
    StateSignal,
    create_default_definitions,
)
from .engine import DEFAULT_TOLERANCE, StateEngine, StateRecognizer
from .errors import (
    InvalidSnapshotError,
    InvalidStateDefinitionError,
    StateError,
    UnknownStateError,
)
from .registry import DEFAULT_REGISTRY, StateRegistry, create_default_registry, registry
from .types import (
    IndicatorSnapshot,
    PriceSnapshot,
    StateEvaluation,
    StateObservation,
    StateResult,
    StateSnapshot,
    StateStatus,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "DEFAULT_TOLERANCE",
    "IndicatorSnapshot",
    "InvalidSnapshotError",
    "InvalidStateDefinitionError",
    "PriceSnapshot",
    "StateDefinition",
    "StateEngine",
    "StateError",
    "StateEvaluation",
    "StateEvaluator",
    "StateObservation",
    "StateRecognizer",
    "StateRegistry",
    "StateResult",
    "StateSignal",
    "StateStatus",
    "StateSnapshot",
    "UnknownStateError",
    "create_default_definitions",
    "create_default_registry",
    "registry",
]
