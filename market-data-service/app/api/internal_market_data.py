"""Internal market-data read and synchronization endpoints."""

from __future__ import annotations

import secrets
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.domain.market_data import (
    Adjustment,
    InvalidMarketDataError,
    normalize_symbol,
)
from app.providers.errors import (
    MarketDataProviderError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.repositories.market_data import (
    DailyBarRepository,
    DividendRepository,
    PersistenceSystemError,
    QuoteRepository,
)
from app.schemas.common import ErrorResponse
from app.schemas.market_data import (
    ApiEnvelope,
    DailyBarData,
    DailyBarsData,
    DailySyncRequest,
    DividendData,
    DividendsData,
    DividendSyncRequest,
    QuoteData,
    QuoteSyncRequest,
    SyncItemData,
    SyncSummaryData,
)
from app.services.sync import (
    DailyBarSyncService,
    DividendSyncService,
    QuoteSyncService,
    SecuritySyncService,
    SyncError,
    SyncSummary,
)

try:
    from sqlalchemy.exc import SQLAlchemyError
except ImportError:  # pragma: no cover - SQLAlchemy is a runtime dependency
    SQLAlchemyError = Exception  # type: ignore[misc,assignment]


router = APIRouter(prefix="/internal", tags=["internal-market-data"])


def get_quote_repository(request: Request) -> QuoteRepository:
    """Resolve the app-scoped quote repository; replaceable in API tests."""

    return request.app.state.quote_repository


def get_dividend_repository(request: Request) -> DividendRepository:
    """Resolve the app-scoped dividend repository; replaceable in tests."""

    return request.app.state.dividend_repository


def get_quote_sync_service(request: Request) -> QuoteSyncService:
    """Resolve the app-scoped quote synchronization service."""

    return request.app.state.quote_sync_service


def get_dividend_sync_service(request: Request) -> DividendSyncService:
    """Resolve the app-scoped dividend synchronization service."""

    return request.app.state.dividend_sync_service


def get_daily_bar_repository(request: Request) -> DailyBarRepository:
    """Resolve the app-scoped daily-bar repository; replaceable in tests."""

    return request.app.state.daily_bar_repository


def get_daily_sync_service(request: Request) -> DailyBarSyncService:
    """Resolve the app-scoped daily sync service; replaceable in tests."""

    return request.app.state.daily_sync_service


def get_security_sync_service(request: Request) -> SecuritySyncService:
    """Resolve the optional app-scoped security sync service."""

    return request.app.state.security_sync_service


async def require_internal_token(request: Request) -> None:
    """Authenticate internal writes without ever permitting anonymous writes."""

    configured = request.app.state.settings.internal_api_token
    if not configured or not configured.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "INTERNAL_AUTH_NOT_CONFIGURED",
                "message": "Internal sync authentication is not configured.",
            },
        )

    provided = request.headers.get("X-Internal-Token")
    if not provided:
        authorization = request.headers.get("Authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() == "bearer":
            provided = value
    if not provided or not secrets.compare_digest(provided, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_INTERNAL_TOKEN", "message": "Internal token is invalid."},
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/quotes/{symbol}")
async def get_quote(
    symbol: str,
    repository: QuoteRepository = Depends(get_quote_repository),  # noqa: B008
) -> JSONResponse:
    """Return the latest persisted quote for one canonical symbol."""

    canonical_symbol = _canonical_symbol(symbol)
    try:
        quote = await repository.get_latest(canonical_symbol)
    except Exception as exc:
        _raise_public_dependency_error(exc)
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUOTE_NOT_FOUND", "message": "Quote was not found."},
        )
    data = QuoteData(
        symbol=quote.symbol,
        price=quote.price,
        change=quote.change,
        change_percent=quote.change_percent,
        timestamp=quote.timestamp,
    )
    return _success(data.model_dump(mode="json"))


@router.get("/dividends/{symbol}")
async def get_dividends(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    repository: DividendRepository = Depends(get_dividend_repository),  # noqa: B008
) -> JSONResponse:
    """Return persisted cash-dividend events for one canonical symbol."""

    canonical_symbol = _canonical_symbol(symbol)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DATE_RANGE", "message": "start must not be after end."},
        )
    try:
        dividends = await repository.list_by_symbol(
            canonical_symbol,
            start=start,
            end=end,
        )
    except Exception as exc:
        _raise_public_dependency_error(exc)
    if not dividends:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "DIVIDENDS_NOT_FOUND", "message": "Dividends were not found."},
        )
    items = [
        DividendData(
            symbol=dividend.symbol,
            date=dividend.date,
            cash_amount=dividend.cash_amount,
        ).model_dump(mode="json")
        for dividend in dividends
    ]
    data = DividendsData(
        symbol=canonical_symbol,
        start=start,
        end=end,
        items=items,
    )
    return _success(data.model_dump(mode="json"))


@router.get("/daily-bars/{symbol}")
async def get_daily_bars(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    adjust: Adjustment = Adjustment.QFQ,
    repository: DailyBarRepository = Depends(get_daily_bar_repository),  # noqa: B008
) -> JSONResponse:
    """Return persisted daily bars filtered by date and adjustment mode."""

    canonical_symbol = _canonical_symbol(symbol)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DATE_RANGE", "message": "start must not be after end."},
        )
    try:
        bars = await repository.list_by_symbol(
            canonical_symbol,
            start=start,
            end=end,
            adjustment=adjust,
        )
    except Exception as exc:
        _raise_public_dependency_error(exc)
    if not bars:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "DAILY_BARS_NOT_FOUND", "message": "Daily bars were not found."},
        )
    items = [
        DailyBarData(
            symbol=bar.symbol,
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            amount=bar.amount,
            adjustment=bar.adjustment,
        ).model_dump(mode="json")
        for bar in bars
    ]
    data = DailyBarsData(
        symbol=canonical_symbol,
        start=start,
        end=end,
        adjustment=adjust,
        items=items,
    )
    return _success(data.model_dump(mode="json"))


@router.post(
    "/sync/daily",
    dependencies=[Depends(require_internal_token)],  # noqa: B008
)
async def sync_daily(
    request: DailySyncRequest,
    service: DailyBarSyncService = Depends(get_daily_sync_service),  # noqa: B008
) -> JSONResponse:
    """Synchronize daily bars with authenticated, idempotent persistence."""

    if request.start > request.end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_DATE_RANGE", "message": "start must not be after end."},
        )
    try:
        summary = await service.sync(
            symbols=request.symbols,
            start=request.start,
            end=request.end,
            adjustment=request.adjustment,
        )
    except Exception as exc:
        _raise_public_dependency_error(exc)
    data = _summary_data(summary)
    return _success(
        data.model_dump(mode="json"),
        success=summary.ok,
        error=None
        if summary.ok
        else ErrorResponse(
            code="PARTIAL_SYNC_FAILURE",
            message="One or more symbols could not be synchronized.",
        ),
    )


@router.post(
    "/sync/quotes",
    dependencies=[Depends(require_internal_token)],  # noqa: B008
)
@router.post(
    "/sync/quote",
    dependencies=[Depends(require_internal_token)],  # noqa: B008
)
async def sync_quotes(
    request: QuoteSyncRequest,
    service: QuoteSyncService = Depends(get_quote_sync_service),  # noqa: B008
) -> JSONResponse:
    """Synchronize latest quote snapshots with authenticated persistence."""

    try:
        summary = await service.sync(symbols=request.symbols)
    except Exception as exc:
        _raise_public_dependency_error(exc)
    data = _summary_data(summary)
    return _success(
        data.model_dump(mode="json"),
        success=summary.ok,
        error=None
        if summary.ok
        else ErrorResponse(
            code="PARTIAL_SYNC_FAILURE",
            message="One or more symbols could not be synchronized.",
        ),
    )


@router.post(
    "/sync/dividends",
    dependencies=[Depends(require_internal_token)],  # noqa: B008
)
@router.post(
    "/sync/dividend",
    dependencies=[Depends(require_internal_token)],  # noqa: B008
)
async def sync_dividends(
    request: DividendSyncRequest,
    service: DividendSyncService = Depends(get_dividend_sync_service),  # noqa: B008
) -> JSONResponse:
    """Synchronize cash-dividend events with authenticated persistence."""

    try:
        summary = await service.sync(symbols=request.symbols)
    except Exception as exc:
        _raise_public_dependency_error(exc)
    data = _summary_data(summary)
    return _success(
        data.model_dump(mode="json"),
        success=summary.ok,
        error=None
        if summary.ok
        else ErrorResponse(
            code="PARTIAL_SYNC_FAILURE",
            message="One or more symbols could not be synchronized.",
        ),
    )


@router.post(
    "/sync/securities",
    dependencies=[Depends(require_internal_token)],  # noqa: B008
)
async def sync_securities(
    service: SecuritySyncService = Depends(get_security_sync_service),  # noqa: B008
) -> JSONResponse:
    """Synchronize security metadata for the daily-bar foreign-key boundary."""

    try:
        summary = await service.sync()
    except Exception as exc:
        _raise_public_dependency_error(exc)
    data = _summary_data(summary)
    return _success(
        data.model_dump(mode="json"),
        success=summary.ok,
        error=None
        if summary.ok
        else ErrorResponse(
            code="PARTIAL_SYNC_FAILURE",
            message="One or more securities could not be synchronized.",
        ),
    )


def _canonical_symbol(value: str) -> str:
    try:
        return normalize_symbol(value)
    except InvalidMarketDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_SYMBOL", "message": "Symbol is invalid."},
        ) from exc


def _success(
    data: Any,
    *,
    success: bool = True,
    error: ErrorResponse | None = None,
    status_code: int = status.HTTP_200_OK,
) -> JSONResponse:
    envelope = ApiEnvelope(success=success, data=data, error=error)
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def _summary_data(summary: SyncSummary) -> SyncSummaryData:
    items = []
    for item in summary.items:
        error = _public_sync_error(item.error)
        items.append(
            SyncItemData(
                symbol=item.symbol,
                status=item.status,
                fetched=item.fetched,
                persisted=item.persisted,
                error=error,
            )
        )
    return SyncSummaryData(
        operation=summary.operation,
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
        ok=summary.ok,
        items=items,
    )


def _public_sync_error(error: SyncError | None) -> ErrorResponse | None:
    if error is None:
        return None
    if error.code.startswith("PROVIDER"):
        message = "Market data provider is unavailable."
    elif error.code.startswith("PERSISTENCE"):
        message = "Market data persistence failed."
    elif error.code == "INVALID_MARKET_DATA":
        message = "Provider returned invalid market data."
    else:
        message = "Synchronization failed."
    return ErrorResponse(code=error.code, message=message)


def _raise_public_dependency_error(exc: Exception) -> None:
    if isinstance(exc, InvalidMarketDataError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_MARKET_DATA", "message": "Market data request is invalid."},
        ) from exc
    if isinstance(
        exc,
        (ProviderConfigurationError, ProviderTimeoutError, ProviderUnavailableError),
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_UNAVAILABLE",
                "message": "Market data provider is unavailable.",
            },
        ) from exc
    if isinstance(exc, MarketDataProviderError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PROVIDER_UNAVAILABLE",
                "message": "Market data provider is unavailable.",
            },
        ) from exc
    if isinstance(exc, (PersistenceSystemError, SQLAlchemyError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Market data storage is unavailable.",
            },
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "INTERNAL_ERROR", "message": "Market data request failed."},
    ) from exc


__all__ = [
    "get_daily_bar_repository",
    "get_daily_sync_service",
    "get_dividend_repository",
    "get_dividend_sync_service",
    "get_quote_repository",
    "get_quote_sync_service",
    "get_security_sync_service",
    "require_internal_token",
    "router",
]
