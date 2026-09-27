"""Application-facing seam for scheduling shared security-data bootstrap work."""

from __future__ import annotations

from typing import Protocol


class SecurityBootstrapScheduler(Protocol):
    def enqueue(self, symbol: str) -> None: ...


class NoopSecurityBootstrapScheduler:
    def enqueue(self, symbol: str) -> None:
        del symbol


__all__ = ["NoopSecurityBootstrapScheduler", "SecurityBootstrapScheduler"]
