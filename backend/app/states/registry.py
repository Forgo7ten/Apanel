"""Name-based state registry with a stable plugin seam."""

from __future__ import annotations

from collections.abc import Iterable

from .definitions import StateDefinition, create_default_definitions
from .errors import UnknownStateError


class StateRegistry:
    """Resolve state definitions without a hard-coded dispatch switch."""

    def __init__(self, definitions: Iterable[StateDefinition] = ()) -> None:
        self._states: dict[str, StateDefinition] = {}
        self._canonical_codes: list[str] = []
        for definition in definitions:
            self.register(definition)

    def register(
        self,
        definition: StateDefinition | object,
        *,
        aliases: Iterable[str] = (),
        replace: bool = False,
    ) -> StateDefinition:
        definition = self._coerce_definition(definition)
        names = (definition.code, *(str(alias).strip().upper() for alias in aliases))
        if any(not name for name in names):
            raise ValueError("state codes and aliases must be non-empty")
        collisions = [name for name in names if name in self._states and not replace]
        if collisions:
            raise ValueError(f"state code already registered: {collisions[0]}")
        for name in names:
            self._states[name] = definition
        if definition.code not in self._canonical_codes:
            self._canonical_codes.append(definition.code)
        return definition

    @staticmethod
    def _coerce_definition(value: StateDefinition | object) -> StateDefinition:
        """Accept a definition or a small class-based plugin object.

        The latter mirrors the indicator registry's plugin ergonomics while
        keeping the registered object immutable and metadata-validated at the
        domain boundary.
        """

        if isinstance(value, StateDefinition):
            return value
        source = getattr(value, "definition", value)
        evaluator = getattr(source, "evaluator", None)
        if evaluator is None:
            evaluator = getattr(source, "evaluate", None)
        fields = ("code", "name", "level", "indicator_type", "metadata")
        if not all(hasattr(source, field) for field in fields) or not callable(evaluator):
            raise TypeError("state registry accepts StateDefinition or state plugin objects")
        return StateDefinition(
            code=source.code,
            name=source.name,
            level=source.level,
            indicator_type=source.indicator_type,
            metadata=source.metadata,
            evaluator=evaluator,
        )

    def get(self, code: str) -> StateDefinition:
        if not isinstance(code, str) or not code.strip():
            raise UnknownStateError("state code must be non-empty")
        key = code.strip().upper()
        try:
            return self._states[key]
        except KeyError as exc:
            raise UnknownStateError(f"unknown state: {code}") from exc

    get_state = get

    def codes(self) -> tuple[str, ...]:
        return tuple(self._canonical_codes)

    def names(self) -> tuple[str, ...]:
        return self.codes()

    def definitions(self) -> tuple[StateDefinition, ...]:
        return tuple(self._states[code] for code in self._canonical_codes)


def create_default_registry() -> StateRegistry:
    return StateRegistry(create_default_definitions())


DEFAULT_REGISTRY = create_default_registry()
registry = DEFAULT_REGISTRY
