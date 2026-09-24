"""Watch-table HTTP controllers.

The controllers only bind request data and return the service projection.  All
ownership, ordering, duplicate and aggregation rules live in the service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.common import SuccessResponse
from app.schemas.watch import (
    AddStockRequest,
    ColumnCreateRequest,
    ColumnReorderRequest,
    ColumnUpdateRequest,
    DeleteData,
    StockReorderRequest,
    TableColumnData,
    WatchTableCreateRequest,
    WatchTableDetailsData,
    WatchTableStockData,
    WatchTableSummaryData,
)
from app.services.watch_table_service import WatchTableService

router = APIRouter()


@router.get("/watch-tables", response_model=SuccessResponse[list[WatchTableSummaryData]])
async def list_watch_tables(
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[WatchTableSummaryData]]:
    return SuccessResponse(data=await WatchTableService(session).list(user.id))


@router.post(
    "/watch-tables",
    response_model=SuccessResponse[WatchTableSummaryData],
    status_code=status.HTTP_201_CREATED,
)
async def create_watch_table(
    payload: WatchTableCreateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[WatchTableSummaryData]:
    result = await WatchTableService(session).create(
        user.id, name=payload.name, description=payload.description
    )
    return SuccessResponse(data=result)


@router.get(
    "/watch-tables/{table_id}", response_model=SuccessResponse[WatchTableDetailsData]
)
async def get_watch_table(
    table_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[WatchTableDetailsData]:
    return SuccessResponse(data=await WatchTableService(session).detail(user.id, table_id))


@router.delete(
    "/watch-tables/{table_id}",
    response_model=SuccessResponse[DeleteData],
)
async def delete_watch_table(
    table_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[DeleteData]:
    await WatchTableService(session).delete(user.id, table_id)
    return SuccessResponse(data=DeleteData())


@router.post(
    "/watch-tables/{table_id}/stocks",
    response_model=SuccessResponse[WatchTableStockData],
    status_code=status.HTTP_201_CREATED,
)
async def add_stock(
    table_id: int,
    payload: AddStockRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[WatchTableStockData]:
    result = await WatchTableService(session).add_stock(user.id, table_id, payload.security_id)
    return SuccessResponse(data=result)


@router.delete(
    "/watch-tables/{table_id}/stocks/{security_id}",
    response_model=SuccessResponse[DeleteData],
)
async def remove_stock(
    table_id: int,
    security_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[DeleteData]:
    await WatchTableService(session).remove_stock(user.id, table_id, security_id)
    return SuccessResponse(data=DeleteData())


@router.put(
    "/watch-tables/{table_id}/stocks/reorder",
    response_model=SuccessResponse[list[WatchTableStockData]],
)
async def reorder_stocks(
    table_id: int,
    payload: StockReorderRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[WatchTableStockData]]:
    result = await WatchTableService(session).reorder_stocks(
        user.id, table_id, [(item.security_id, item.position) for item in payload.items]
    )
    return SuccessResponse(data=result)


@router.get(
    "/watch-tables/{table_id}/columns",
    response_model=SuccessResponse[list[TableColumnData]],
)
async def list_columns(
    table_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[TableColumnData]]:
    return SuccessResponse(data=await WatchTableService(session).list_columns(user.id, table_id))


@router.post(
    "/watch-tables/{table_id}/columns",
    response_model=SuccessResponse[TableColumnData],
    status_code=status.HTTP_201_CREATED,
)
async def add_column(
    table_id: int,
    payload: ColumnCreateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[TableColumnData]:
    result = await WatchTableService(session).add_column(user.id, table_id, payload)
    return SuccessResponse(data=result)


@router.put(
    "/watch-tables/{table_id}/columns/reorder",
    response_model=SuccessResponse[list[TableColumnData]],
)
async def reorder_columns_for_table(
    table_id: int,
    payload: ColumnReorderRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[list[TableColumnData]]:
    result = await WatchTableService(session).reorder_columns(
        user.id, table_id, payload.column_ids
    )
    return SuccessResponse(data=result)


@router.put(
    "/columns/{column_id}",
    response_model=SuccessResponse[TableColumnData],
)
@router.patch(
    "/columns/{column_id}",
    response_model=SuccessResponse[TableColumnData],
)
async def update_column(
    column_id: int,
    payload: ColumnUpdateRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[TableColumnData]:
    result = await WatchTableService(session).update_column(user.id, column_id, payload)
    return SuccessResponse(data=result)


@router.delete(
    "/columns/{column_id}",
    response_model=SuccessResponse[DeleteData],
)
async def delete_column(
    column_id: int,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> SuccessResponse[DeleteData]:
    await WatchTableService(session).delete_column(user.id, column_id)
    return SuccessResponse(data=DeleteData())


__all__ = ["router"]
