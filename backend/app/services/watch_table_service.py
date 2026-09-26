"""Application service for user watch tables and dynamic columns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.indicators.parameters import (
    IndicatorVariant,
    canonicalize_parameters,
    normalize_indicator_type,
    normalize_json_parameters,
    select_snapshot_variant,
)
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
        stocks: list[WatchTableStockData] = []
        for membership in table.symbols:
            security = membership.security
            quote = await self.security_repository.latest_quote(security.id)
            snapshots = await self.security_repository.latest_indicators(security.id)
            states = await self.security_repository.current_states(security.id)
            indicators, column_values = await self._indicator_projections(
                table.columns,
                snapshots,
                security.id,
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
                    column_values=column_values,
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

    async def _indicator_projections(
        self,
        columns: Sequence[TableColumn],
        snapshots: Sequence[Any],
        security_id: int,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Build values only for configured columns with matching snapshots.

        A security may have more than one persisted snapshot for a logical
        indicator over time, and MA is also persisted as one snapshot holding
        several periods.  The column parameters are therefore part of the
        lookup key; selecting by ``indicator_type`` alone would silently show
        a value for the wrong user-selected period.
        """

        indicator_columns = [
            column
            for column in columns
            if column.column_type.upper() == "INDICATOR" and column.indicator_type
        ]
        counts: dict[str, int] = {}
        for column in indicator_columns:
            indicator_type = _normalize_indicator_type(column.indicator_type)
            counts[indicator_type] = counts.get(indicator_type, 0) + 1

        projected: dict[str, dict[str, Any]] = {}
        column_values: dict[str, dict[str, Any]] = {}
        for column in indicator_columns:
            indicator_type = _normalize_indicator_type(column.indicator_type)
            parameters = _normalize_indicator_parameters(
                indicator_type,
                column.parameters,
                fill_defaults=True,
            )
            calculation_parameters = _calculation_parameters(parameters)
            if indicator_type == "DIVIDEND_YIELD":
                value = await self._dividend_yield_projection(security_id)
                column_value = _column_value_from_mapping(
                    column,
                    indicator_type,
                    parameters,
                    value,
                )
            else:
                variant = _matching_snapshot(snapshots, indicator_type, calculation_parameters)
                column_value = _column_value_from_variant(
                    column,
                    indicator_type,
                    parameters,
                    variant,
                )
                value = (
                    _snapshot_projection(variant, indicator_type, calculation_parameters)
                    if variant is not None
                    else None
                )
            column_values[str(column.id)] = column_value
            if value is None:
                continue

            # Keep the historical indicator-type key for one column of a type.
            # When a table contains MA5 and MA20, stable column-id keys prevent
            # one projection from overwriting the other; the first projection
            # remains available under the legacy type alias for compatibility.
            if counts[indicator_type] == 1:
                projected[indicator_type] = value
            else:
                projected.setdefault(indicator_type, value)
                projected[str(column.id)] = value
        return projected, column_values

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
        next_parameters = column.parameters
        if request.parameters is not None:
            if column.indicator_type is None:
                next_parameters = normalize_json_parameters(request.parameters)
            else:
                next_parameters = _normalize_indicator_parameters(
                    _normalize_indicator_type(column.indicator_type),
                    request.parameters,
                    fill_defaults=True,
                )
        if request.view_mode is not None:
            _validate_view_mode(request.view_mode)
            column.view_mode = request.view_mode
        if request.parameters is not None:
            column.parameters = next_parameters
        if request.visible is not None:
            column.visible = request.visible
        if request.hidden is not None:
            column.visible = not request.hidden
        if request.width is not None:
            column.width = request.width
        if column.indicator_type:
            _validate_field_selector(
                _normalize_indicator_type(column.indicator_type),
                column.parameters,
                column.view_mode.upper(),
            )
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
    if indicator_type is None:
        parameters: dict[str, Any] = {}
    else:
        indicator_type = _normalize_indicator_type(indicator_type)
        parameters = _normalize_indicator_parameters(
            indicator_type,
            request.parameters,
            fill_defaults=True,
        )
        _validate_field_selector(indicator_type, parameters, view_mode)
    return column_type, indicator_type, parameters, view_mode


def _validate_view_mode(view_mode: str) -> None:
    if view_mode.upper() not in _VIEW_MODES:
        raise ApiError("INVALID_VIEW_MODE", "View mode is invalid.", 400)


def _normalize_indicator_type(value: str | None) -> str:
    """Return the persistence spelling used by indicator snapshots."""

    if value is None:
        return ""
    return normalize_indicator_type(value)


def _normalize_indicator_parameters(
    indicator_type: str,
    parameters: Any,
    *,
    fill_defaults: bool,
) -> dict[str, Any]:
    """Normalize supported column parameters to the snapshot contract.

    Indicator calculation owns the actual formulas.  This boundary only
    validates and canonicalizes the JSON key/value representation used by a
    watch column and an ``IndicatorSnapshot`` lookup.
    """

    if not isinstance(parameters, dict):
        raise ApiError(
            "INVALID_INDICATOR_PARAMETERS",
            "Indicator parameters must be an object.",
            400,
        )
    normalized_type = _normalize_indicator_type(indicator_type)
    field = next(
        (
            value
            for key, value in parameters.items()
            if isinstance(key, str) and key.strip().lower() == "field"
        ),
        None,
    )
    calculation_parameters = {
        key: value
        for key, value in parameters.items()
        if not isinstance(key, str) or key.strip().lower() != "field"
    }
    normalized = canonicalize_parameters(
        normalized_type,
        calculation_parameters,
        fill_defaults=fill_defaults,
    )
    if field is not None:
        normalized["field"] = _normalize_field_selector(field)
    return normalized


def _normalize_field_selector(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(
            "INVALID_INDICATOR_FIELD",
            "Indicator field must be a non-empty string.",
            400,
        )
    return value.strip().lower()


def _validate_field_selector(
    indicator_type: str,
    parameters: Mapping[str, Any],
    view_mode: str,
) -> None:
    if view_mode not in {"NUMBER", "DELTA"}:
        return
    calculation_parameters = _calculation_parameters(parameters)
    if not _is_composite_parameters(indicator_type, calculation_parameters):
        return
    field = parameters.get("field")
    if field is None:
        raise ApiError(
            "INDICATOR_FIELD_REQUIRED",
            "NUMBER and DELTA views for composite indicators require field.",
            400,
        )
    ma_periods = parameters.get("periods", ())
    if "period" in parameters:
        ma_periods = (parameters["period"],)
    allowed = {
        "MA": {f"ma{period}" for period in ma_periods},
        "KDJ": {"rsv", "k", "d", "j"},
        "BOLL": {"upper", "middle", "lower", "width"},
        "MACD": {"diff", "dea", "histogram"},
    }.get(indicator_type)
    if allowed and str(field).lower() not in allowed:
        raise ApiError(
            "INVALID_INDICATOR_FIELD",
            f"Indicator field {field!r} is not available for {indicator_type}.",
            400,
        )


def _is_composite_parameters(indicator_type: str, parameters: Mapping[str, Any]) -> bool:
    if indicator_type in {"BOLL", "KDJ", "MACD"}:
        return True
    return indicator_type == "MA" and "period" not in parameters


def _calculation_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in parameters.items()
        if str(key).strip().lower() != "field"
    }


def _matching_snapshot(
    snapshots: Sequence[Any], indicator_type: str, parameters: dict[str, Any]
) -> IndicatorVariant | None:
    for snapshot in snapshots:
        if _normalize_indicator_type(snapshot.indicator_type) != indicator_type:
            continue
        try:
            variant = select_snapshot_variant(snapshot, indicator_type, parameters)
        except ApiError:
            # Persisted snapshots are shared data.  A malformed legacy row
            # must be treated as a miss, not as a reason to fail every table.
            continue
        if variant is not None:
            return variant
    return None


def _snapshot_parameters_match(
    indicator_type: str,
    column_parameters: dict[str, Any],
    snapshot_parameters: dict[str, Any],
) -> bool:
    if indicator_type != "MA":
        return column_parameters == snapshot_parameters
    requested_period = column_parameters.get("period")
    if requested_period is not None:
        periods = snapshot_parameters.get("periods")
        if periods is not None:
            return requested_period in periods
        return snapshot_parameters.get("period") == requested_period
    requested_periods = column_parameters.get("periods")
    if requested_periods is not None:
        snapshot_periods = snapshot_parameters.get("periods")
        if snapshot_periods is not None:
            return requested_periods == snapshot_periods
        return (
            len(requested_periods) == 1
            and snapshot_parameters.get("period") == requested_periods[0]
        )
    return not snapshot_parameters


def _snapshot_projection(
    variant: IndicatorVariant,
    indicator_type: str,
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    values = _json_numbers(variant.values)
    delta = _json_numbers(variant.delta) if variant.delta is not None else None
    if not isinstance(values, dict):
        return None
    current_values = dict(values)

    if indicator_type == "MA" and parameters.get("period") is not None:
        period = parameters["period"]
        value_key = _mapping_key(values, f"MA{period}")
        delta_key = value_key or f"MA{period}"
        if value_key is None and len(values) == 1 and "value" in values:
            value_key = "value"
            delta_key = "value"
        if value_key is None:
            return None
        current_value = values[value_key]
        current_delta = delta.get(delta_key) if isinstance(delta, dict) else delta
        projection = dict(values)
        projection["value"] = current_value
        projection["current_value"] = current_value
        projection["delta"] = current_delta
        return projection

    if indicator_type in {"RSI", "PROJECTED_MA"} and "value" in values:
        current_value = values["value"]
        current_delta = delta.get("value") if isinstance(delta, dict) else delta
        projection = dict(values)
        projection["current_value"] = current_value
        projection["delta"] = current_delta
        return projection

    values["current_value"] = current_values
    if delta is not None:
        values["delta"] = delta
    return values


def _column_value_from_variant(
    column: TableColumn,
    indicator_type: str,
    parameters: Mapping[str, Any],
    variant: IndicatorVariant | None,
) -> dict[str, Any]:
    if variant is None:
        return _unavailable_column_value(column, indicator_type, parameters, "SNAPSHOT_NOT_FOUND")
    return _column_value_from_values(
        column,
        indicator_type,
        parameters,
        variant.values,
        variant.previous_values,
        variant.delta,
    )


def _column_value_from_mapping(
    column: TableColumn,
    indicator_type: str,
    parameters: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    numeric_values = {
        str(key): float(value)
        for key, value in values.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    if not numeric_values:
        return _unavailable_column_value(column, indicator_type, parameters, "VALUE_NOT_FOUND")
    return _column_value_from_values(column, indicator_type, parameters, numeric_values, None, None)


def _column_value_from_values(
    column: TableColumn,
    indicator_type: str,
    parameters: Mapping[str, Any],
    values: Mapping[str, Any],
    previous_values: Mapping[str, Any] | None,
    delta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    mode = column.view_mode.upper()
    normalized_values = {str(key): float(value) for key, value in values.items()}
    normalized_previous = (
        {str(key): float(value) for key, value in previous_values.items()}
        if isinstance(previous_values, Mapping)
        else {}
    )
    normalized_delta = (
        {str(key): float(value) for key, value in delta.items()}
        if isinstance(delta, Mapping)
        else {}
    )
    result: dict[str, Any] = {
        "column_id": column.id,
        "view_mode": mode,
        "indicator_type": indicator_type,
        "parameters": _json_numbers(dict(parameters)),
        "available": True,
    }
    if mode == "COMPOSITE":
        result["fields"] = {
            key: _field_value(
                normalized_values.get(key),
                normalized_previous.get(key),
                normalized_delta.get(key),
            )
            for key in normalized_values
        }
        return result

    field = parameters.get("field")
    if field is None:
        if "value" in normalized_values:
            field = "value"
        elif len(normalized_values) == 1:
            field = next(iter(normalized_values))
    value_key = (
        field
        if isinstance(field, str) and field in normalized_values
        else next(
            (
                key
                for key in normalized_values
                if isinstance(field, str) and key.casefold() == field.casefold()
            ),
            None,
        )
    )
    if value_key is None:
        return _unavailable_column_value(
            column,
            indicator_type,
            parameters,
            "INDICATOR_FIELD_NOT_FOUND",
        )
    scalar = _field_value(
        normalized_values.get(value_key),
        normalized_previous.get(value_key),
        normalized_delta.get(value_key),
    )
    result.update(scalar)
    return result


def _field_value(
    value: float | None,
    previous_value: float | None,
    delta: float | None,
) -> dict[str, Any]:
    return {
        "value": value,
        "previous_value": previous_value,
        "delta": delta,
        "direction": (
            "UP"
            if delta is not None and delta > 0
            else "DOWN"
            if delta is not None and delta < 0
            else "FLAT"
        ),
    }


def _unavailable_column_value(
    column: TableColumn,
    indicator_type: str,
    parameters: Mapping[str, Any],
    error_code: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "column_id": column.id,
        "view_mode": column.view_mode.upper(),
        "indicator_type": indicator_type,
        "parameters": _json_numbers(dict(parameters)),
        "available": False,
        "error_code": error_code,
        "value": None,
        "previous_value": None,
        "delta": None,
        "direction": "FLAT",
    }
    if column.view_mode.upper() == "COMPOSITE":
        result["fields"] = {}
    return result


def _mapping_key(values: dict[str, Any], expected: str) -> str | None:
    """Find a persisted value key without changing its public spelling."""

    if expected in values:
        return expected
    folded = expected.casefold()
    return next(
        (key for key in values if isinstance(key, str) and key.casefold() == folded),
        None,
    )


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
            "current_value": value,
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
