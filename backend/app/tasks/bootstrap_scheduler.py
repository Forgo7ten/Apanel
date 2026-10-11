"""Celery adapter for the application bootstrap scheduling seam."""

from __future__ import annotations

from fastapi import BackgroundTasks

from app.services.bootstrap_scheduler import SecurityBootstrapScheduler

from .jobs import enqueue_security_bootstrap


class CelerySecurityBootstrapScheduler(SecurityBootstrapScheduler):
    def enqueue(self, symbol: str) -> None:
        enqueue_security_bootstrap(symbol)


class DeferredSecurityBootstrapScheduler(SecurityBootstrapScheduler):
    """Queue only after the HTTP response; never block the request on Redis."""

    def __init__(self, background_tasks: BackgroundTasks) -> None:
        self.background_tasks = background_tasks

    def enqueue(self, symbol: str) -> None:
        self.background_tasks.add_task(enqueue_security_bootstrap, symbol)


__all__ = ["CelerySecurityBootstrapScheduler", "DeferredSecurityBootstrapScheduler"]
