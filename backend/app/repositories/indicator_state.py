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

    async def _upsert_many(self, model, payloads, conflict_columns, update_columns):
        if not payloads:
            return []
        unique = {
            tuple(payload[column] for column in conflict_columns): payload for payload in payloads
        }
        ordered = [unique[key] for key in sorted(unique)]
        bind = self.session.get_bind()
        factory = postgres_insert if bind.dialect.name == "postgresql" else sqlite_insert
        result = []
        size = max(1, min(400, 30000 // max(len(item) for item in ordered)))
        for offset in range(0, len(ordered), size):
            values = [
                {
                    ("metadata_json" if key == "metadata" else key): value
                    for key, value in row.items()
                }
                for row in ordered[offset : offset + size]
            ]
            statement = factory(model).values(values)
            statement = statement.on_conflict_do_update(
                index_elements=list(conflict_columns),
                set_={column: statement.excluded[column] for column in update_columns},
            ).returning(model)
            rows = (
                (
                    await self.session.execute(
                        statement, execution_options={"populate_existing": True}
                    )
                )
                .scalars()
                .all()
            )
            result.extend(rows)
        return result

    async def upsert_snapshots(self, payloads):
        return await self._upsert_many(
            IndicatorSnapshot,
            payloads,
            ("security_id", "trade_date", "indicator_type", "adjust_type"),
            ("parameters", "values", "previous_values", "delta"),
        )

    async def upsert_definitions(self, payloads):
        return await self._upsert_many(
            StateDefinition,
            payloads,
            ("state_code",),
            ("indicator_type", "name", "description", "level", "metadata", "enabled"),
        )

    async def get_security(self, symbol: str) -> Security | None:
        return (
            await self.session.execute(select(Security).where(Security.symbol == symbol))
        ).scalar_one_or_none()

    async def get_daily_bars(
        self,
        security_id: int,
        *,
        adjustment: str | None = None,
        end: date | None = None,
        start: date | None = None,
    ) -> list[DailyBar]:
        statement: Select[tuple[DailyBar]] = select(DailyBar).where(
            DailyBar.security_id == security_id
        )
        if adjustment is not None:
            statement = statement.where(DailyBar.adjust_type == adjustment)
        if start is not None:
            statement = statement.where(DailyBar.trade_date >= start)
        if end is not None:
            statement = statement.where(DailyBar.trade_date <= end)
        statement = statement.order_by(DailyBar.trade_date.asc(), DailyBar.id.asc())
        return list(
            (
                await self.session.execute(statement.execution_options(populate_existing=True))
            ).scalars()
        )

    async def upsert_snapshot(
        self,
        *,
        security_id: int,
        trade_date: date,
        indicator_type: str,
        adjust_type: str = "qfq",
        parameters: dict[str, Any],
        values: dict[str, Any],
        previous_values: dict[str, Any] | None,
        delta: dict[str, Any] | None,
    ) -> IndicatorSnapshot:
        payload = {
            "security_id": security_id,
            "trade_date": trade_date,
            "indicator_type": indicator_type,
            "adjust_type": adjust_type,
            "parameters": parameters,
            "values": values,
            "previous_values": previous_values,
            "delta": delta,
        }
        return (await self.upsert_snapshots([payload]))[0]

    async def list_snapshots(
        self,
        security_id: int,
        *,
        indicator_type: str | None = None,
        adjustment: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[IndicatorSnapshot]:
        statement: Select[tuple[IndicatorSnapshot]] = select(IndicatorSnapshot).where(
            IndicatorSnapshot.security_id == security_id
        )
        if indicator_type is not None:
            statement = statement.where(IndicatorSnapshot.indicator_type == indicator_type)
        if adjustment is not None:
            statement = statement.where(IndicatorSnapshot.adjust_type == adjustment)
        if start is not None:
            statement = statement.where(IndicatorSnapshot.trade_date >= start)
        if end is not None:
            statement = statement.where(IndicatorSnapshot.trade_date <= end)
        statement = statement.order_by(
            IndicatorSnapshot.trade_date.asc(), IndicatorSnapshot.indicator_type.asc()
        )
        return list(
            (
                await self.session.execute(statement.execution_options(populate_existing=True))
            ).scalars()
        )

    async def latest_snapshot_date(
        self, security_id: int, *, adjustment: str | None = None
    ) -> date | None:
        return (
            await self.session.execute(
                select(func.max(IndicatorSnapshot.trade_date)).where(
                    IndicatorSnapshot.security_id == security_id,
                    *(() if adjustment is None else (IndicatorSnapshot.adjust_type == adjustment,)),
                )
            )
        ).scalar_one_or_none()

    async def latest_snapshots(
        self, security_id: int, *, adjustment: str | None = None
    ) -> list[IndicatorSnapshot]:
        latest_date = (
            await self.session.execute(
                select(func.max(IndicatorSnapshot.trade_date)).where(
                    IndicatorSnapshot.security_id == security_id,
                    *(() if adjustment is None else (IndicatorSnapshot.adjust_type == adjustment,)),
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
                        *(
                            ()
                            if adjustment is None
                            else (IndicatorSnapshot.adjust_type == adjustment,)
                        ),
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
        return (await self.upsert_definitions([payload]))[0]

    async def list_state_definitions(self, *, enabled_only: bool = True) -> list[StateDefinition]:
        statement: Select[tuple[StateDefinition]] = select(StateDefinition)
        if enabled_only:
            statement = statement.where(StateDefinition.enabled.is_(True))
        statement = statement.order_by(StateDefinition.id.asc())
        return list(
            (
                await self.session.execute(statement.execution_options(populate_existing=True))
            ).scalars()
        )

    async def _upsert(
        self,
        model: type[IndicatorSnapshot] | type[StateDefinition],
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
        model: type[IndicatorSnapshot] | type[StateDefinition],
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
