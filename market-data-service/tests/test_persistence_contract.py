from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.dialects import postgresql

from app.domain.market_data import Adjustment, DailyBar, Dividend, Quote, Security
from app.models.market_data import (
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
