"""Celery adapter for the application bootstrap scheduling seam."""

from __future__ import annotations

from app.services.bootstrap_scheduler import SecurityBootstrapScheduler

from .jobs import enqueue_security_bootstrap


class CelerySecurityBootstrapScheduler(SecurityBootstrapScheduler):
    def enqueue(self, symbol: str) -> None:
        enqueue_security_bootstrap(symbol)


__all__ = ["CelerySecurityBootstrapScheduler"]
