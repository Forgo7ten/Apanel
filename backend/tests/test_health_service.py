import asyncio

from app.services.health_service import HealthService


async def test_health_service_reports_all_dependencies_healthy() -> None:
    calls: list[str] = []

    async def postgres_probe() -> None:
        calls.append("postgres")

    async def redis_probe() -> None:
        calls.append("redis")

    data = await HealthService(
        database_probe=postgres_probe,
        redis_probe=redis_probe,
        service_name="test",
        version="0.0.0",
    ).check()

    assert data.status == "healthy"
    assert calls == ["postgres", "redis"]
    assert all(item.status == "healthy" for item in data.dependencies.values())


async def test_health_service_is_degraded_when_one_probe_fails() -> None:
    async def postgres_probe() -> None:
        raise OSError("database is offline")

    async def redis_probe() -> None:
        return None

    data = await HealthService(
        database_probe=postgres_probe,
        redis_probe=redis_probe,
        service_name="test",
        version="0.0.0",
    ).check()

    assert data.status == "degraded"
    assert data.dependencies["postgres"].status == "unhealthy"
    assert data.dependencies["postgres"].detail == "unavailable"
    assert data.dependencies["redis"].status == "healthy"


async def test_health_service_marks_a_timed_out_probe_unhealthy() -> None:
    async def hanging_probe() -> None:
        await asyncio.sleep(0.05)

    async def redis_probe() -> None:
        return None

    data = await HealthService(
        database_probe=hanging_probe,
        redis_probe=redis_probe,
        service_name="test",
        version="0.0.0",
        probe_timeout_seconds=0.001,
    ).check()

    assert data.status == "degraded"
    assert data.dependencies["postgres"].status == "unhealthy"
    assert data.dependencies["postgres"].detail == "timeout"
