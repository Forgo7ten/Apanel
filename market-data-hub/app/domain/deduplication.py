"""Idempotent market facts: equal duplicates collapse; conflicting facts are rejected."""

from collections.abc import Callable, Iterable
from typing import Any

from .market_data import InvalidMarketDataError


def unique_facts[T](records: Iterable[T], *, key: Callable[[T], Any]) -> tuple[T, ...]:
    """Never infer authority from response order when no source revision is supplied."""
    unique: dict[Any, T] = {}
    for record in records:
        identity = key(record)
        if identity in unique and unique[identity] != record:
            raise InvalidMarketDataError("conflicting market records for one identity")
        unique[identity] = record
    return tuple(unique[identity] for identity in sorted(unique))
