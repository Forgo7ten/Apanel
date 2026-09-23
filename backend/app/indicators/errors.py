"""Domain errors raised by the pure indicator engine."""

from __future__ import annotations


class IndicatorError(ValueError):
    """Base class for input, parameter, and calculation contract errors."""


class InvalidInputError(IndicatorError):
    """Raised when a candle or series cannot be used for calculation."""


class InvalidParameterError(IndicatorError):
    """Raised when an indicator parameter is outside its supported domain."""


class InsufficientDataError(IndicatorError):
    """Raised when a series does not contain enough observations."""

    def __init__(self, required: int, available: int, *, indicator: str | None = None) -> None:
        self.required = required
        self.available = available
        self.indicator = indicator
        label = f" for {indicator}" if indicator else ""
        super().__init__(
            f"insufficient data{label}: requires at least {required} observations, "
            f"received {available}"
        )


class UnknownIndicatorError(IndicatorError):
    """Raised when a registry lookup has no indicator with the requested name."""


class ResultValidationError(IndicatorError):
    """Raised when an indicator or plugin returns an invalid result."""
