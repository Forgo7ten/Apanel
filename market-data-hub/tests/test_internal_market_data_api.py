from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.internal_market_data import (
    get_daily_bar_repository,
    get_daily_sync_service,
    get_quote_repository,
)
from app.core.config import Settings
from app.domain.market_data import Adjustment, DailyBar, Quote
from app.main import create_app
from app.services.sync import SyncItemResult, SyncSummary


class FakeQuoteRepository:
    async def get_latest(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            price=Decimal("10.500000000000000001"),
            change=Decimal("0.10"),
            timestamp=datetime(2026, 9, 23, 2, 30, tzinfo=UTC),
        )


class FakeDailyBarRepository:
    async def list_by_symbol(self, symbol, *, start=None, end=None, adjustment=Adjustment.QFQ):
        return (
            DailyBar(
                symbol=symbol,
                trade_date=date(2026, 9, 23),
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("10.5"),
                volume=Decimal("100"),
                adjustment=adjustment,
            ),
        )


class FakeDailySyncService:
    calls: list[dict[str, object]] = []

    async def sync(self, **kwargs):
        self.calls.append(kwargs)
        return SyncSummary(
            operation="daily_bar",
            items=(SyncItemResult(symbol="600519", status="success", fetched=1, persisted=1),),
        )


def make_app() -> object:
    app = create_app(Settings(app_env="test", internal_api_token="test-token"))
    app.dependency_overrides[get_quote_repository] = lambda: FakeQuoteRepository()
    app.dependency_overrides[get_daily_bar_repository] = lambda: FakeDailyBarRepository()
    app.dependency_overrides[get_daily_sync_service] = lambda: FakeDailySyncService()
    return app


def test_internal_reads_use_uniform_success_envelope_and_preserve_decimal_text() -> None:
    app = make_app()
    try:
        with TestClient(app) as client:
            quote_response = client.get("/internal/quotes/SH.600519")
            bars_response = client.get(
                "/internal/daily-bars/600519?start=2026-09-01&end=2026-09-30&adjust=none"
            )
    finally:
        app.dependency_overrides.clear()

    assert quote_response.status_code == 200
    assert quote_response.json()["success"] is True
    assert quote_response.json()["data"]["price"] == "10.500000000000000001"
    assert bars_response.status_code == 200
    assert bars_response.json()["data"]["adjustment"] == "none"


def test_internal_sync_rejects_anonymous_writes_and_accepts_internal_token() -> None:
    app = make_app()
    try:
        with TestClient(app) as client:
            payload = {
                "symbols": ["600519"],
                "start": "2026-09-01",
                "end": "2026-09-30",
                "adjust": "none",
            }
            unauthorized = client.post("/internal/sync/daily", json=payload)
            authorized = client.post(
                "/internal/sync/daily",
                json=payload,
                headers={"X-Internal-Token": "test-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {
        "success": False,
        "data": None,
        "error": {"code": "INVALID_INTERNAL_TOKEN", "message": "Internal token is invalid."},
    }
    assert authorized.status_code == 200
    assert authorized.json()["success"] is True
    assert authorized.json()["data"]["succeeded"] == 1


def test_internal_api_returns_uniform_validation_error() -> None:
    app = make_app()
    try:
        with TestClient(app) as client:
            response = client.get("/internal/daily-bars/600519?adjust=invalid")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
