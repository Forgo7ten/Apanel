from fastapi.testclient import TestClient

from app.api.health import get_health_service
from app.main import app
from app.schemas.health import DependencyHealth, HealthData


class FakeHealthService:
    async def check(self) -> HealthData:
        return HealthData(
            service="test-market-data",
            version="0.0.0",
            status="healthy",
            dependencies={
                "postgres": DependencyHealth(status="healthy", latency_ms=0.1),
                "redis": DependencyHealth(status="healthy", latency_ms=0.1),
            },
        )


class UnhealthyHealthService:
    async def check(self) -> HealthData:
        return HealthData(
            service="test-market-data",
            version="0.0.0",
            status="degraded",
            dependencies={
                "postgres": DependencyHealth(status="healthy", latency_ms=0.1),
                "redis": DependencyHealth(status="unhealthy", detail="timeout"),
            },
        )


def test_health_endpoint_is_available_at_root_health_path() -> None:
    app.dependency_overrides[get_health_service] = lambda: FakeHealthService()
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["data"]["service"] == "test-market-data"


def test_health_endpoint_returns_503_when_dependency_is_unhealthy() -> None:
    app.dependency_overrides[get_health_service] = lambda: UnhealthyHealthService()
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert body["data"]["dependencies"]["redis"]["detail"] == "timeout"
