from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domain.market_data import Adjustment, DailyBar, Dividend, Quote, Security
from app.models.market_data import (
    Base,
    DailyBarModel,
    DividendEventModel,
    QuoteSnapshotModel,
    SecurityModel,
)
from app.repositories.market_data import (
    SqlAlchemyDailyBarRepository,
    SqlAlchemyDividendEventRepository,
    SqlAlchemyQuoteSnapshotRepository,
    SqlAlchemySecurityRepository,
)
from app.services.sync import SecuritySyncService


class RecordingTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class RecordingSession:
    def __init__(self):
        self.statements = []

    def begin(self):
        return RecordingTransaction()

    async def execute(self, statement):
        self.statements.append(statement)


class RecordingSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        class SessionContext:
            async def __aenter__(inner_self):
                return self.session

            async def __aexit__(inner_self, exc_type, exc, traceback):
                return False

        return SessionContext()


class SyncTransactionAdapter:
    """Async context seam backed by SQLAlchemy's real synchronous transaction."""

    def __init__(self, transaction):
        self._transaction = transaction

    async def __aenter__(self):
        self._transaction.__enter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return self._transaction.__exit__(exc_type, exc, traceback)


class SyncSessionAdapter:
    """Keep this test dependency-free while exercising a real SQLite session."""

    def __init__(self, engine):
        self._session = Session(engine)

    @property
    def bind(self):
        return self._session.bind

    def begin(self):
        return SyncTransactionAdapter(self._session.begin())

    async def execute(self, statement):
        return self._session.execute(statement)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self._session.close()


class SyncSessionFactory:
    def __init__(self, engine):
        self._engine = engine

    def __call__(self):
        return SyncSessionAdapter(self._engine)


def test_market_models_preserve_decimal_and_utc_columns() -> None:
    assert SecurityModel.__table__.c.symbol.unique
    assert DailyBarModel.__table__.c.open.type.asdecimal
    assert DailyBarModel.__table__.c.close.type.asdecimal
    assert QuoteSnapshotModel.__table__.c.timestamp.type.timezone
    assert DividendEventModel.__table__.c.cash_amount.type.asdecimal


def test_postgresql_upserts_have_domain_unique_conflicts() -> None:
    security = Security(
        symbol="600519",
        name="贵州茅台",
        market="SH",
        security_type="STOCK",
    )
    daily_bar = DailyBar(
        symbol="600519",
        trade_date=date(2026, 9, 23),
        open=Decimal("10.00"),
        high=Decimal("11.00"),
        low=Decimal("9.00"),
        close=Decimal("10.50"),
        volume=Decimal("100"),
        adjustment=Adjustment.NONE,
    )
    quote = Quote(
        symbol="600519",
        price=Decimal("10.50"),
        timestamp=datetime(2026, 9, 23, 2, 30, tzinfo=UTC),
    )
    dividend = Dividend(symbol="600519", date=date(2026, 6, 30), cash_amount=Decimal("2.00"))

    security_sql = str(
        SqlAlchemySecurityRepository.build_upsert_statement([security]).compile(
            dialect=postgresql.dialect()
        )
    )
    daily_sql = str(
        SqlAlchemyDailyBarRepository.build_upsert_statement(
            [{"security_id": 1, "record": daily_bar}]
        ).compile(dialect=postgresql.dialect())
    )
    quote_sql = str(
        SqlAlchemyQuoteSnapshotRepository.build_upsert_statement(
            [{"security_id": 1, "record": quote}]
        ).compile(dialect=postgresql.dialect())
    )
    dividend_sql = str(
        SqlAlchemyDividendEventRepository.build_upsert_statement(
            [{"security_id": 1, "record": dividend}]
        ).compile(dialect=postgresql.dialect())
    )

    assert "ON CONFLICT (symbol) DO UPDATE" in security_sql
    assert "ON CONFLICT (security_id, trade_date, adjust_type) DO UPDATE" in daily_sql
    assert "ON CONFLICT (security_id, timestamp) DO UPDATE" in quote_sql
    assert "ON CONFLICT (security_id, date) DO UPDATE" in dividend_sql


async def test_security_repository_uses_one_explicit_transaction_for_a_batch() -> None:
    session = RecordingSession()
    repository = SqlAlchemySecurityRepository(RecordingSessionFactory(session))
    await repository.upsert_many(
        [
            Security(
                symbol="600519",
                name="贵州茅台",
                market="SH",
                security_type="STOCK",
            )
        ]
    )

    assert len(session.statements) == 1
    assert "ON CONFLICT (symbol) DO UPDATE" in str(
        session.statements[0].compile(dialect=postgresql.dialect())
    )


@pytest.mark.asyncio
async def test_security_sync_rolls_back_a_real_sqlalchemy_batch_on_a_later_constraint_failure(
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_second_security
            BEFORE INSERT ON securities
            WHEN NEW.symbol = '000001'
            BEGIN
                SELECT RAISE(ABORT, 'second security rejected');
            END
            """
        )

    repository = SqlAlchemySecurityRepository(SyncSessionFactory(engine))

    class Provider:
        async def get_symbols(self):
            return (
                Security(
                    symbol="600519",
                    name="贵州茅台",
                    market="SH",
                    security_type="STOCK",
                ),
                Security(
                    symbol="000001",
                    name="平安银行",
                    market="SZ",
                    security_type="STOCK",
                ),
            )

    result = await SecuritySyncService(provider=Provider(), repository=repository).sync()

    assert result.failed == 1
    assert result.items[0].error is not None
    assert result.items[0].error.code == "PERSISTENCE_ERROR"
    with engine.connect() as connection:
        assert connection.execute(select(SecurityModel)).all() == []
    engine.dispose()
