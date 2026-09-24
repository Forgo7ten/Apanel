"""Public security search and shared market-data endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import SuccessResponse
from app.schemas.security import DailyBarData, DividendYieldData, QuoteData, SecurityData
from app.services.security_service import SecurityService

router = APIRouter()


@router.get("/securities/search", response_model=SuccessResponse[list[SecurityData]])
async def search_securities(
    q: str = Query(default="", max_length=128),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[SecurityData]]:
    securities = await SecurityService(session).search(q)
    return SuccessResponse(data=[_security_data(item) for item in securities])


@router.get("/securities/{symbol}", response_model=SuccessResponse[SecurityData])
async def get_security(
    symbol: str,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[SecurityData]:
    security = await SecurityService(session).get(symbol)
    return SuccessResponse(data=_security_data(security))


@router.get("/securities/{symbol}/quote", response_model=SuccessResponse[QuoteData])
async def get_quote(
    symbol: str,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[QuoteData]:
    quote = await SecurityService(session).quote(symbol)
    price = float(quote.price)
    return SuccessResponse(
        data=QuoteData(
            price=price,
            value=price,
            change=float(quote.change) if quote.change is not None else None,
            change_percent=(
                float(quote.change_percent) if quote.change_percent is not None else None
            ),
            timestamp=quote.timestamp,
        )
    )


@router.get(
    "/securities/{symbol}/dividend-yield",
    response_model=SuccessResponse[DividendYieldData],
)
async def get_dividend_yield(
    symbol: str,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[DividendYieldData]:
    """Return the TTM cash-dividend yield and its calculation inputs."""

    result = await SecurityService(session).dividend_yield(symbol)
    return SuccessResponse(
        data=DividendYieldData(
            dividend_total=result.dividend_total,
            price=result.price,
            dividend_yield=result.dividend_yield,
            as_of=result.as_of,
            price_source=result.price_source,
            window_start=result.window_start,
            window_end=result.window_end,
            dividend_event_count=result.dividend_event_count,
        )
    )


@router.get(
    "/securities/{symbol}/daily-bars",
    response_model=SuccessResponse[list[DailyBarData]],
)
async def get_daily_bars(
    symbol: str,
    start_date: date | None = Query(default=None),  # noqa: B008
    end_date: date | None = Query(default=None),  # noqa: B008
    adjust_type: str | None = Query(default=None),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[DailyBarData]]:
    bars = await SecurityService(session).daily_bars(
        symbol,
        start_date=start_date,
        end_date=end_date,
        adjust_type=adjust_type,
    )
    return SuccessResponse(
        data=[
            DailyBarData(
                date=bar.trade_date,
                trade_date=bar.trade_date,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
                amount=float(bar.amount) if bar.amount is not None else None,
                adjust_type=bar.adjust_type,
            )
            for bar in bars
        ]
    )


def _security_data(security) -> SecurityData:
    return SecurityData(
        id=security.id,
        security_id=security.id,
        symbol=security.symbol,
        name=security.name,
        market=security.market,
        exchange=security.exchange,
        security_type=security.security_type,
        type=security.security_type,
        status=security.status,
    )


__all__ = ["router"]
