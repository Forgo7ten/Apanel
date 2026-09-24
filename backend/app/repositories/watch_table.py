"""Persistence queries for user-owned watch tables."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import TableColumn, WatchTable, WatchTableSymbol


class WatchTableRepository:
    """Repository that requires the owning user id for every user-data query."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: int) -> list[tuple[WatchTable, int]]:
        statement = (
            select(WatchTable, func.count(WatchTableSymbol.id))
            .outerjoin(WatchTableSymbol, WatchTableSymbol.watch_table_id == WatchTable.id)
            .where(WatchTable.user_id == user_id)
            .group_by(WatchTable.id)
            .order_by(WatchTable.created_at, WatchTable.id)
        )
        rows = (await self.session.execute(statement)).all()
        return [(table, int(count)) for table, count in rows]

    async def get_owned(
        self,
        table_id: int,
        user_id: int,
        *,
        with_details: bool = False,
    ) -> WatchTable | None:
        statement: Select[tuple[WatchTable]] = select(WatchTable).where(
            WatchTable.id == table_id,
            WatchTable.user_id == user_id,
        )
        if with_details:
            statement = statement.options(
                selectinload(WatchTable.columns),
                selectinload(WatchTable.symbols).joinedload(WatchTableSymbol.security),
            )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_column_owned(self, column_id: int, user_id: int) -> TableColumn | None:
        statement = (
            select(TableColumn)
            .options(joinedload(TableColumn.watch_table))
            .join(WatchTable, WatchTable.id == TableColumn.watch_table_id)
            .where(TableColumn.id == column_id, WatchTable.user_id == user_id)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_columns_owned(self, table_id: int, user_id: int) -> list[TableColumn] | None:
        table = await self.get_owned(table_id, user_id)
        if table is None:
            return None
        return list(
            (
                await self.session.execute(
                    select(TableColumn)
                    .where(TableColumn.watch_table_id == table_id)
                    .order_by(TableColumn.position.asc(), TableColumn.id.asc())
                )
            ).scalars()
        )

    async def find_symbol(self, table_id: int, security_id: int) -> WatchTableSymbol | None:
        return (
            await self.session.execute(
                select(WatchTableSymbol).where(
                    WatchTableSymbol.watch_table_id == table_id,
                    WatchTableSymbol.security_id == security_id,
                )
            )
        ).scalar_one_or_none()

    async def next_symbol_position(self, table_id: int) -> int:
        current = (
            await self.session.execute(
                select(func.max(WatchTableSymbol.position)).where(
                    WatchTableSymbol.watch_table_id == table_id
                )
            )
        ).scalar_one()
        return int(current) + 1 if current is not None else 0

    async def list_symbols(self, table_id: int) -> list[WatchTableSymbol]:
        return list(
            (
                await self.session.execute(
                    select(WatchTableSymbol)
                    .options(joinedload(WatchTableSymbol.security))
                    .where(WatchTableSymbol.watch_table_id == table_id)
                    .order_by(WatchTableSymbol.position.asc(), WatchTableSymbol.id.asc())
                )
            ).scalars()
        )

    async def find_duplicate_column(
        self,
        table_id: int,
        *,
        column_type: str,
        indicator_type: str | None,
        parameters: dict[str, Any],
        exclude_id: int | None = None,
    ) -> TableColumn | None:
        statement = select(TableColumn).where(
            TableColumn.watch_table_id == table_id,
            TableColumn.column_type == column_type,
            TableColumn.indicator_type == indicator_type,
        )
        if exclude_id is not None:
            statement = statement.where(TableColumn.id != exclude_id)
        candidates = list((await self.session.execute(statement)).scalars())
        for candidate in candidates:
            if candidate.parameters == parameters:
                return candidate
        return None

    async def next_column_position(self, table_id: int) -> int:
        current = (
            await self.session.execute(
                select(func.max(TableColumn.position)).where(TableColumn.watch_table_id == table_id)
            )
        ).scalar_one()
        return int(current) + 1 if current is not None else 0

    async def reorder_columns(
        self, table_id: int, column_ids: Sequence[int]
    ) -> list[TableColumn]:
        rows = list(
            (
                await self.session.execute(
                    select(TableColumn).where(TableColumn.watch_table_id == table_id)
                )
            ).scalars()
        )
        by_id = {row.id: row for row in rows}
        for position, column_id in enumerate(column_ids):
            by_id[column_id].position = position
        return sorted(rows, key=lambda row: (row.position, row.id))


__all__ = ["WatchTableRepository"]
