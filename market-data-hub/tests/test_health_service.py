import asyncio

from app.services.health_service import HealthService


async def test_health_service_reports_dependency_statuses() -> None:
    async def postgres_probe() -> None:
        return None

    async def redis_probe() -> None:
        raise OSError("redis is offline")

    data = await HealthService(
        database_probe=postgres_probe,
        redis_probe=redis_probe,
        service_name="test-market-data",
        version="0.0.0",
    ).check()

    assert data.status == "degraded"
    assert data.dependencies["postgres"].status == "healthy"
    assert data.dependencies["redis"].status == "unhealthy"


async def test_health_service_marks_a_timed_out_probe_unhealthy() -> None:
    async def postgres_probe() -> None:
        await asyncio.sleep(0.05)

    async def redis_probe() -> None:
        return None

    data = await HealthService(
        database_probe=postgres_probe,
        redis_probe=redis_probe,
        service_name="test-market-data",
        version="0.0.0",
        probe_timeout_seconds=0.001,
    ).check()

    assert data.status == "degraded"
    assert data.dependencies["postgres"].status == "unhealthy"
    assert data.dependencies["postgres"].detail == "timeout"
