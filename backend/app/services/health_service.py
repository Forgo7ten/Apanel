"""Application-level infrastructure health orchestration."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable

from app.schemas.health import DependencyHealth, HealthData

logger = logging.getLogger(__name__)

Probe = Callable[[], Awaitable[object] | object]


class HealthService:
    """Run independent dependency probes and compose one service snapshot.

    Probes are constructor-injected so unit tests can exercise the complete
    health contract without requiring a running database or Redis instance.
    """

    def __init__(
        self,
        *,
        database_probe: Probe,
        redis_probe: Probe,
        service_name: str,
        version: str,
        probe_timeout_seconds: float = 2.0,
    ) -> None:
        self._database_probe = database_probe
        self._redis_probe = redis_probe
        self._service_name = service_name
        self._version = version
        self._probe_timeout_seconds = probe_timeout_seconds

    async def check(self) -> HealthData:
        """Return a snapshot; one dependency failure does not hide the other."""

        postgres = await self._run_probe("postgres", self._database_probe)
        redis = await self._run_probe("redis", self._redis_probe)
        dependencies = {"postgres": postgres, "redis": redis}
        status = (
            "healthy"
            if all(item.status == "healthy" for item in dependencies.values())
            else "degraded"
        )
        return HealthData(
            service=self._service_name,
            version=self._version,
            status=status,
            dependencies=dependencies,
        )

    async def _run_probe(self, name: str, probe: Probe) -> DependencyHealth:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._invoke_probe(probe), timeout=self._probe_timeout_seconds)
        except TimeoutError:
            logger.warning("Health probe timed out: %s", name)
            return DependencyHealth(
                status="unhealthy",
                latency_ms=_elapsed_ms(started),
                detail="timeout",
            )
        except Exception:
            logger.warning("Health probe failed: %s", name, exc_info=True)
            return DependencyHealth(
                status="unhealthy",
                latency_ms=_elapsed_ms(started),
                detail="unavailable",
            )
        return DependencyHealth(status="healthy", latency_ms=_elapsed_ms(started))

    @staticmethod
    async def _invoke_probe(probe: Probe) -> None:
        result = probe()
        if inspect.isawaitable(result):
            await result


def _elapsed_ms(started: float) -> float:
    """Round timing noise to a stable, readable precision."""

    return round((time.perf_counter() - started) * 1000, 2)
