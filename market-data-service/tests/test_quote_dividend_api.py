from datetime import date

from fastapi.testclient import TestClient

from app.api.internal_market_data import (
    get_dividend_repository,
    get_dividend_sync_service,
    get_quote_sync_service,
)
from app.core.config import Settings
from app.domain.market_data import Dividend
from app.main import create_app
from app.services.sync import SyncItemResult, SyncSummary


class FakeDividendRepository:
    async def list_by_symbol(self, symbol, *, start=None, end=None):
        return (
            Dividend(symbol=symbol, date=date(2026, 6, 30), cash_amount="2.00"),
        )


class FakeQuoteSyncService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def sync(self, *, symbols):
        self.calls.append(tuple(symbols))
        return SyncSummary(
            operation="quote",
            items=(SyncItemResult(symbol="600519", status="success", fetched=1, persisted=1),),
        )


class FakeDividendSyncService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def sync(self, *, symbols):
        self.calls.append(tuple(symbols))
        return SyncSummary(
            operation="dividend",
            items=(SyncItemResult(symbol="600519", status="success", fetched=1, persisted=1),),
        )


def make_app() -> tuple[object, FakeQuoteSyncService, FakeDividendSyncService]:
    app = create_app(Settings(app_env="test", internal_api_token="test-token"))
    quote_service = FakeQuoteSyncService()
    dividend_service = FakeDividendSyncService()
    app.dependency_overrides[get_dividend_repository] = lambda: FakeDividendRepository()
    app.dependency_overrides[get_quote_sync_service] = lambda: quote_service
    app.dependency_overrides[get_dividend_sync_service] = lambda: dividend_service
    return app, quote_service, dividend_service


def test_dividend_query_serializes_persisted_events_and_date_filters() -> None:
    app, _, _ = make_app()
    try:
        with TestClient(app) as client:
            response = client.get(
                "/internal/dividends/SZ.600519?start=2026-01-01&end=2026-12-31"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["items"][0]["cash_amount"] == "2.00"


def test_quote_and_dividend_sync_require_token_and_delegate_to_services() -> None:
    app, quote_service, dividend_service = make_app()
    try:
        with TestClient(app) as client:
            unauthorized = client.post("/internal/sync/quotes", json={"symbols": ["600519"]})
            quote_response = client.post(
                "/internal/sync/quotes",
                json={"symbols": ["600519"]},
                headers={"X-Internal-Token": "test-token"},
            )
            dividend_response = client.post(
                "/internal/sync/dividends",
                json={"symbols": ["600519"]},
                headers={"Authorization": "Bearer test-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert unauthorized.status_code == 401
    assert quote_response.status_code == 200
    assert quote_response.json()["data"]["operation"] == "quote"
    assert dividend_response.status_code == 200
    assert dividend_response.json()["data"]["operation"] == "dividend"
    assert quote_service.calls == [("600519",)]
    assert dividend_service.calls == [("600519",)]


def test_dividend_query_rejects_reversed_date_range() -> None:
    app, _, _ = make_app()
    try:
        with TestClient(app) as client:
            response = client.get(
                "/internal/dividends/600519?start=2026-12-31&end=2026-01-01"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DATE_RANGE"
