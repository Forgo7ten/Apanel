"""Application errors mapped to the public API error envelope."""

from __future__ import annotations


class ApiError(Exception):
    """A safe, expected application error for an HTTP request."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
