"""Persistence seams for indicator snapshots and state observations."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DailyBar,
    IndicatorSnapshot,
    IndicatorState,
    Security,
    StateDefinition,
)


class IndicatorStateRepository:
    """Read/write repository shared by indicator and state services.

    Upserts use the database business keys, so retries after a worker restart
    replace the same observation instead of appending duplicate rows.  The
    small generic fallback keeps unit tests and other SQLAlchemy dialects
    useful while PostgreSQL and SQLite use native conflict handling.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_security(self, symbol: str) -> Security | None:
        return (
            await self.session.execute(select(Security).where(Security.symbol == symbol))
        ).scalar_one_or_none()

    async def get_daily_bars(
        self,
        security_id: int,
        *,
        adjustment: str | None = None,
    ) -> list[DailyBar]:
        statement: Select[tuple[DailyBar]] = select(DailyBar).where(
            DailyBar.security_id == security_id
        )
        if adjustment is not None:
            statement = statement.where(DailyBar.adjust_type == adjustment)
        statement = statement.order_by(DailyBar.trade_date.asc(), DailyBar.id.asc())
        return list((await self.session.execute(statement)).scalars())

    async def upsert_snapshot(
        self,
        *,
        security_id: int,
        trade_date: date,
        indicator_type: str,
        parameters: dict[str, Any],
        values: dict[str, Any],
        previous_values: dict[str, Any] | None,
        delta: dict[str, Any] | None,
    ) -> IndicatorSnapshot:
        payload = {
            "security_id": security_id,
            "trade_date": trade_date,
            "indicator_type": indicator_type,
            "parameters": parameters,
            "values": values,
            "previous_values": previous_values,
            "delta": delta,
        }
        await self._upsert(
            IndicatorSnapshot,
            payload,
            conflict_columns=("security_id", "trade_date", "indicator_type"),
            update_columns=("parameters", "values", "previous_values", "delta"),
        )
        return (
            await self.session.execute(
                select(IndicatorSnapshot).where(
                    IndicatorSnapshot.security_id == security_id,
                    IndicatorSnapshot.trade_date == trade_date,
                    IndicatorSnapshot.indicator_type == indicator_type,
                )
            )
        ).scalar_one()

    async def list_snapshots(
        self,
        security_id: int,
        *,
        indicator_type: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[IndicatorSnapshot]:
        statement: Select[tuple[IndicatorSnapshot]] = select(IndicatorSnapshot).where(
            IndicatorSnapshot.security_id == security_id
        )
        if indicator_type is not None:
            statement = statement.where(IndicatorSnapshot.indicator_type == indicator_type)
        if start is not None:
            statement = statement.where(IndicatorSnapshot.trade_date >= start)
        if end is not None:
            statement = statement.where(IndicatorSnapshot.trade_date <= end)
        statement = statement.order_by(
            IndicatorSnapshot.trade_date.asc(), IndicatorSnapshot.indicator_type.asc()
        )
        return list((await self.session.execute(statement)).scalars())

    async def latest_snapshots(self, security_id: int) -> list[IndicatorSnapshot]:
        latest_date = (
            await self.session.execute(
                select(func.max(IndicatorSnapshot.trade_date)).where(
                    IndicatorSnapshot.security_id == security_id
                )
            )
        ).scalar_one_or_none()
        if latest_date is None:
            return []
        return list(
            (
                await self.session.execute(
                    select(IndicatorSnapshot)
                    .where(
                        IndicatorSnapshot.security_id == security_id,
                        IndicatorSnapshot.trade_date == latest_date,
                    )
                    .order_by(IndicatorSnapshot.indicator_type.asc())
                )
            ).scalars()
        )

    async def upsert_state_definition(
        self,
        *,
        state_code: str,
        indicator_type: str,
        name: str,
        description: str | None,
        level: str,
        metadata: dict[str, Any],
        enabled: bool = True,
    ) -> StateDefinition:
        payload = {
            "state_code": state_code,
            "indicator_type": indicator_type,
            "name": name,
            "description": description,
            "level": level,
            "metadata": metadata,
            "enabled": enabled,
        }
        await self._upsert(
            StateDefinition,
            payload,
            conflict_columns=("state_code",),
            update_columns=(
                "indicator_type",
                "name",
                "description",
                "level",
                "metadata",
                "enabled",
            ),
        )
        return (
            await self.session.execute(
                select(StateDefinition).where(StateDefinition.state_code == state_code)
            )
        ).scalar_one()

    async def list_state_definitions(self, *, enabled_only: bool = True) -> list[StateDefinition]:
        statement: Select[tuple[StateDefinition]] = select(StateDefinition)
        if enabled_only:
            statement = statement.where(StateDefinition.enabled.is_(True))
        statement = statement.order_by(StateDefinition.id.asc())
        return list((await self.session.execute(statement)).scalars())

    async def upsert_state(
        self,
        *,
        security_id: int,
        trade_date: date,
        state_code: str,
        indicator_type: str,
        status: str,
        metadata: dict[str, Any],
    ) -> IndicatorState:
        payload = {
            "security_id": security_id,
            "trade_date": trade_date,
            "state_code": state_code,
            "indicator_type": indicator_type,
            "status": status,
            "metadata": metadata,
        }
        await self._upsert(
            IndicatorState,
            payload,
            conflict_columns=("security_id", "trade_date", "state_code"),
            update_columns=("indicator_type", "status", "metadata"),
        )
        return (
            await self.session.execute(
                select(IndicatorState).where(
                    IndicatorState.security_id == security_id,
                    IndicatorState.trade_date == trade_date,
                    IndicatorState.state_code == state_code,
                )
            )
        ).scalar_one()

    async def list_states(
        self,
        security_id: int,
        *,
        start: date | None = None,
        end: date | None = None,
        active_only: bool = False,
    ) -> list[IndicatorState]:
        statement: Select[tuple[IndicatorState]] = select(IndicatorState).where(
            IndicatorState.security_id == security_id
        )
        if start is not None:
            statement = statement.where(IndicatorState.trade_date >= start)
        if end is not None:
            statement = statement.where(IndicatorState.trade_date <= end)
        if active_only:
            statement = statement.where(IndicatorState.status == "ACTIVE")
        statement = statement.order_by(IndicatorState.trade_date.asc(), IndicatorState.id.asc())
        return list((await self.session.execute(statement)).scalars())

    async def latest_state_date(self, security_id: int) -> date | None:
        return (
            await self.session.execute(
                select(func.max(IndicatorState.trade_date)).where(
                    IndicatorState.security_id == security_id
                )
            )
        ).scalar_one_or_none()

    async def previous_state(
        self,
        security_id: int,
        state_code: str,
        trade_date: date,
    ) -> IndicatorState | None:
        return (
            await self.session.execute(
                select(IndicatorState)
                .where(
                    IndicatorState.security_id == security_id,
                    IndicatorState.state_code == state_code,
                    IndicatorState.trade_date < trade_date,
                )
                .order_by(IndicatorState.trade_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _upsert(
        self,
        model: type[IndicatorSnapshot] | type[StateDefinition] | type[IndicatorState],
        payload: dict[str, Any],
        *,
        conflict_columns: tuple[str, ...],
        update_columns: tuple[str, ...],
    ) -> None:
        bind = self.session.bind
        dialect_name = bind.dialect.name if bind is not None else ""
        table = model.__table__
        if dialect_name == "postgresql":
            statement = postgres_insert(table).values(**payload)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(table).values(**payload)
        else:
            await self._fallback_upsert(
                model,
                payload,
                conflict_columns=conflict_columns,
                update_columns=update_columns,
            )
            return
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=list(conflict_columns),
            # ``values`` is a method on the insert object, so bracket lookup
            # is required for this column (and is unambiguous for all names).
            set_={column: excluded[column] for column in update_columns},
        )
        await self.session.execute(statement)

    async def _fallback_upsert(
        self,
        model: type[IndicatorSnapshot] | type[StateDefinition] | type[IndicatorState],
        payload: dict[str, Any],
        *,
        conflict_columns: tuple[str, ...],
        update_columns: tuple[str, ...],
    ) -> None:
        clauses = [getattr(model, column) == payload[column] for column in conflict_columns]
        row = (await self.session.execute(select(model).where(and_(*clauses)))).scalar_one_or_none()
        if row is None:
            self.session.add(model(**payload))
            return
        for column in update_columns:
            setattr(row, column, payload[column])


__all__ = ["IndicatorStateRepository"]
