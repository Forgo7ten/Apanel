"""Application service for user watch tables and dynamic columns."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import TableColumn, WatchTable, WatchTableSymbol
from app.repositories.security import SecurityRepository
from app.repositories.watch_table import WatchTableRepository
from app.schemas.watch import (
    ColumnCreateRequest,
    ColumnUpdateRequest,
    CurrentStateData,
    PriceData,
    TableColumnData,
    WatchTableDetailsData,
    WatchTableStockData,
    WatchTableSummaryData,
)
from app.services.dividend_service import DividendYieldResult, DividendYieldService

_COLUMN_TYPES = {"PRICE", "INDICATOR", "STATE"}
_VIEW_MODES = {"NUMBER", "DELTA", "STATUS", "COMPOSITE"}


class WatchTableService:
    """Own all watch-table business rules outside HTTP controllers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = WatchTableRepository(session)
        self.security_repository = SecurityRepository(session)
        self.dividend_yield_service = DividendYieldService(self.security_repository)

    async def list(self, user_id: int) -> list[WatchTableSummaryData]:
        rows = await self.repository.list_for_user(user_id)
        return [self._summary(table, count) for table, count in rows]

    async def create(
        self, user_id: int, *, name: str, description: str | None = None
    ) -> WatchTableSummaryData:
        normalized_name = _normalize_name(name)
        if not normalized_name:
            raise ApiError("INVALID_REQUEST", "Watch table name is required.", 400)
        table = WatchTable(
            user_id=user_id,
            name=normalized_name,
            description=description.strip() if description else None,
        )
        self.session.add(table)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError(
                "WATCH_TABLE_EXISTS", "A watch table with this name already exists.", 409
            ) from exc
        await self.session.refresh(table)
        return self._summary(table, 0)

    async def delete(self, user_id: int, table_id: int) -> None:
        table = await self._owned(table_id, user_id)
        await self.session.delete(table)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError(
                "WATCH_TABLE_DELETE_FAILED", "Watch table could not be deleted.", 409
            ) from exc

    async def detail(self, user_id: int, table_id: int) -> WatchTableDetailsData:
        table = await self._owned(table_id, user_id, with_details=True)
        has_dividend_yield_column = any(
            column.column_type.upper() == "INDICATOR"
            and (column.indicator_type or "").upper() == "DIVIDEND_YIELD"
            for column in table.columns
        )
        stocks: list[WatchTableStockData] = []
        for membership in table.symbols:
            security = membership.security
            quote = await self.security_repository.latest_quote(security.id)
            snapshots = await self.security_repository.latest_indicators(security.id)
            states = await self.security_repository.current_states(security.id)
            indicators = {
                snapshot.indicator_type: _json_numbers(snapshot.values)
                for snapshot in snapshots
            }
            if has_dividend_yield_column:
                indicators["DIVIDEND_YIELD"] = await self._dividend_yield_projection(
                    security.id
                )
            price = _price_data(quote)
            state_data = [_state_data(state) for state in states]
            stocks.append(
                WatchTableStockData(
                    security_id=security.id,
                    symbol=security.symbol,
                    name=security.name,
                    market=security.market,
                    security=_security_data(security),
                    price=price,
                    indicators=indicators,
                    indicator_values=indicators,
                    values=indicators,
                    states=state_data,
                )
            )
        columns = [_column_data(column) for column in table.columns]
        return WatchTableDetailsData(
            id=table.id,
            name=table.name,
            description=table.description,
            stock_count=len(stocks),
            columns=columns,
            stocks=stocks,
        )

    async def _dividend_yield_projection(self, security_id: int) -> dict[str, Any]:
        """Project the explainable yield value for a dynamic watch column."""

        try:
            result = await self.dividend_yield_service.calculate_for_security(security_id)
        except ApiError as error:
            if error.code not in {"PRICE_NOT_FOUND", "INVALID_PRICE", "INVALID_DIVIDEND_DATA"}:
                raise
            return {
                "value": None,
                "yield": None,
                "dividend_yield": None,
                "dividend_total": None,
                "price": None,
                "as_of": None,
                "price_source": None,
                "error_code": error.code,
            }
        return _dividend_yield_data(result)

    async def add_stock(
        self, user_id: int, table_id: int, security_id: int
    ) -> WatchTableStockData:
        table = await self._owned(table_id, user_id)
        security = await self.security_repository.get_by_id(security_id)
        if security is None:
            raise ApiError("SECURITY_NOT_FOUND", "Security was not found.", 404)
        if await self.repository.find_symbol(table.id, security.id) is not None:
            raise ApiError("WATCH_STOCK_EXISTS", "Security is already in this watch table.", 409)
        membership = WatchTableSymbol(
            watch_table_id=table.id,
            security_id=security.id,
            position=await self.repository.next_symbol_position(table.id),
        )
        self.session.add(membership)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError(
                "WATCH_STOCK_EXISTS", "Security is already in this watch table.", 409
            ) from exc
        await self.session.refresh(security)
        return await self._stock_projection(membership, security)

    async def remove_stock(self, user_id: int, table_id: int, security_id: int) -> None:
        table = await self._owned(table_id, user_id)
        membership = await self.repository.find_symbol(table.id, security_id)
        if membership is None:
            raise ApiError("WATCH_STOCK_NOT_FOUND", "Security is not in this watch table.", 404)
        await self.session.delete(membership)
        await self.session.commit()

    async def reorder_stocks(
        self, user_id: int, table_id: int, items: Sequence[tuple[int, int]]
    ) -> list[WatchTableStockData]:
        table = await self._owned(table_id, user_id)
        rows = await self.repository.list_symbols(table.id)
        expected = {row.security_id for row in rows}
        received = [security_id for security_id, _ in items]
        positions = [position for _, position in items]
        if (
            len(received) != len(set(received))
            or set(received) != expected
            or len(positions) != len(set(positions))
        ):
            raise ApiError(
                "INVALID_STOCK_ORDER",
                "Stock order must contain every stock exactly once.",
                400,
            )
        for security_id, position in items:
            next(row for row in rows if row.security_id == security_id).position = position
        await self.session.commit()
        rows = await self.repository.list_symbols(table.id)
        return [await self._stock_projection(row, row.security) for row in rows]

    async def list_columns(self, user_id: int, table_id: int) -> list[TableColumnData]:
        columns = await self.repository.get_columns_owned(table_id, user_id)
        if columns is None:
            raise ApiError("WATCH_TABLE_NOT_FOUND", "Watch table was not found.", 404)
        return [_column_data(column) for column in columns]

    async def add_column(
        self, user_id: int, table_id: int, request: ColumnCreateRequest
    ) -> TableColumnData:
        table = await self._owned(table_id, user_id)
        column_type, indicator_type, parameters, view_mode = _validate_column_request(request)
        duplicate = await self.repository.find_duplicate_column(
            table.id,
            column_type=column_type,
            indicator_type=indicator_type,
            parameters=parameters,
        )
        if duplicate is not None:
            raise ApiError("WATCH_COLUMN_EXISTS", "This column already exists.", 409)
        position = await self.repository.next_column_position(table.id)
        if request.position is not None:
            position = min(request.position, position)
            existing = await self.repository.get_columns_owned(table.id, user_id) or []
            for column in existing:
                if column.position >= position:
                    column.position += 1
        column = TableColumn(
            watch_table_id=table.id,
            column_type=column_type,
            indicator_type=indicator_type,
            parameters=parameters,
            view_mode=view_mode,
            position=position,
            visible=request.visible,
            width=request.width,
        )
        self.session.add(column)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ApiError("WATCH_COLUMN_EXISTS", "This column already exists.", 409) from exc
        await self.session.refresh(column)
        return _column_data(column)

    async def update_column(
        self, user_id: int, column_id: int, request: ColumnUpdateRequest
    ) -> TableColumnData:
        column = await self.repository.get_column_owned(column_id, user_id)
        if column is None:
            raise ApiError("WATCH_COLUMN_NOT_FOUND", "Column was not found.", 404)
        if request.view_mode is not None:
            _validate_view_mode(request.view_mode)
            column.view_mode = request.view_mode
        if request.parameters is not None:
            column.parameters = request.parameters
        if request.visible is not None:
            column.visible = request.visible
        if request.hidden is not None:
            column.visible = not request.hidden
        if request.width is not None:
            column.width = request.width
        next_position = request.position if request.position is not None else request.order
        if next_position is not None:
            await self._move_column(column, next_position)
        duplicate = await self.repository.find_duplicate_column(
            column.watch_table_id,
            column_type=column.column_type,
            indicator_type=column.indicator_type,
            parameters=column.parameters,
            exclude_id=column.id,
        )
        if duplicate is not None:
            await self.session.rollback()
            raise ApiError("WATCH_COLUMN_EXISTS", "This column already exists.", 409)
        await self.session.commit()
        await self.session.refresh(column)
        return _column_data(column)

    async def delete_column(self, user_id: int, column_id: int) -> None:
        column = await self.repository.get_column_owned(column_id, user_id)
        if column is None:
            raise ApiError("WATCH_COLUMN_NOT_FOUND", "Column was not found.", 404)
        await self.session.delete(column)
        await self.session.commit()

    async def reorder_columns(
        self, user_id: int, table_id: int, column_ids: Sequence[int]
    ) -> list[TableColumnData]:
        table = await self._owned(table_id, user_id)
        columns = await self.repository.get_columns_owned(table.id, user_id) or []
        expected = {column.id for column in columns}
        if len(column_ids) != len(set(column_ids)) or set(column_ids) != expected:
            raise ApiError(
                "INVALID_COLUMN_ORDER",
                "Column order must contain every column exactly once.",
                400,
            )
        ordered = await self.repository.reorder_columns(table.id, column_ids)
        await self.session.commit()
        return [_column_data(column) for column in ordered]

    async def _owned(
        self, table_id: int, user_id: int, *, with_details: bool = False
    ) -> WatchTable:
        table = await self.repository.get_owned(table_id, user_id, with_details=with_details)
        if table is None:
            raise ApiError("WATCH_TABLE_NOT_FOUND", "Watch table was not found.", 404)
        return table

    async def _move_column(self, column: TableColumn, position: int) -> None:
        position = max(0, position)
        rows = await self.repository.get_columns_owned(
            column.watch_table_id, column.watch_table.user_id
        )
        if rows is None:
            return
        rows = [row for row in rows if row.id != column.id]
        position = min(position, len(rows))
        for index, row in enumerate(rows):
            row.position = index if index < position else index + 1
        column.position = position

    async def _stock_projection(
        self, membership: WatchTableSymbol, security
    ) -> WatchTableStockData:
        return WatchTableStockData(
            security_id=security.id,
            symbol=security.symbol,
            name=security.name,
            market=security.market,
            security=_security_data(security),
        )

    @staticmethod
    def _summary(table: WatchTable, stock_count: int) -> WatchTableSummaryData:
        return WatchTableSummaryData(
            id=table.id,
            name=table.name,
            description=table.description,
            stock_count=stock_count,
            created_at=table.created_at,
            updated_at=table.updated_at,
        )


def _normalize_name(value: str) -> str:
    return " ".join(value.strip().split())


def _validate_column_request(
    request: ColumnCreateRequest,
) -> tuple[str, str | None, dict[str, Any], str]:
    column_type = request.column_type.upper()
    if column_type not in _COLUMN_TYPES:
        raise ApiError("INVALID_COLUMN_TYPE", "Column type is invalid.", 400)
    view_mode = request.view_mode.upper()
    _validate_view_mode(view_mode)
    indicator_type = request.indicator_type.upper() if request.indicator_type else None
    if column_type == "INDICATOR" and not indicator_type:
        raise ApiError("INVALID_COLUMN", "Indicator columns require indicator_type.", 400)
    if column_type == "PRICE":
        indicator_type = None
    return column_type, indicator_type, dict(request.parameters), view_mode


def _validate_view_mode(view_mode: str) -> None:
    if view_mode.upper() not in _VIEW_MODES:
        raise ApiError("INVALID_VIEW_MODE", "View mode is invalid.", 400)


def _security_data(security) -> Any:
    from app.schemas.security import SecurityData

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


def _price_data(quote) -> PriceData | None:
    if quote is None:
        return None
    change = _number(quote.change)
    return PriceData(
        value=_number(quote.price),
        price=_number(quote.price),
        change=change,
        delta=change,
        change_percent=_number(quote.change_percent),
        direction=(
            "UP"
            if change is not None and change > 0
            else "DOWN"
            if change is not None and change < 0
            else "FLAT"
        ),
        timestamp=quote.timestamp,
    )


def _state_data(state) -> CurrentStateData:
    metadata = _json_numbers(state.metadata_json)
    return CurrentStateData(
        state_id=state.state_code,
        state_code=state.state_code,
        title=str(metadata.get("name", state.state_code)),
        level=str(metadata.get("level", "INFO")),
        indicator_type=state.indicator_type,
        status=state.status,
        active=bool(metadata.get("active", state.status == "ACTIVE")),
        transition=bool(metadata.get("transition", False)),
        trade_date=state.trade_date,
        metadata=metadata,
    )


def _dividend_yield_data(result: DividendYieldResult) -> dict[str, Any]:
    """Keep both generic indicator ``value`` and yield explanation fields."""

    value = result.dividend_yield
    return _json_numbers(
        {
            "value": value,
            "yield": value,
            "dividend_yield": value,
            "dividend_total": result.dividend_total,
            "price": result.price,
            "as_of": result.as_of,
            "price_source": result.price_source,
            "window_start": result.window_start,
            "window_end": result.window_end,
            "dividend_event_count": result.dividend_event_count,
        }
    )


def _column_data(column: TableColumn) -> TableColumnData:
    return TableColumnData(
        id=column.id,
        column_type=column.column_type,
        type=column.indicator_type or column.column_type,
        indicator_type=column.indicator_type,
        parameters=_json_numbers(column.parameters),
        view_mode=column.view_mode,
        position=column.position,
        order=column.position,
        visible=column.visible,
        hidden=not column.visible,
        width=column.width,
    )


def _json_numbers(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_numbers(item) for item in value]
    return value


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


__all__ = ["WatchTableService"]
