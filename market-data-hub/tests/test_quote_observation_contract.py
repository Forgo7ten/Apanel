"""TDX raw quote clock is opaque: persisted timestamp denotes retrieval."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.providers.eltdx import EltdxProvider
from app.schemas.market_data import QuoteData


@pytest.mark.asyncio
async def test_quote_uses_receipt_time_not_opaque_time_raw():
    class Quotes:
        def get_snapshots(self, codes):
            return [
                SimpleNamespace(
                    code="600519",
                    last_price=10,
                    change=0.5,
                    change_pct=5,
                    time_raw=111111,
                )
            ]

    before = datetime.now(UTC)
    items = await EltdxProvider(SimpleNamespace(quotes=Quotes())).get_quotes(("600519",))
    after = datetime.now(UTC)
    assert len(items) == 1
    assert before <= items[0].timestamp <= after
    serialized = QuoteData(
        symbol=items[0].symbol,
        price=items[0].price,
        timestamp=items[0].timestamp,
    ).model_dump(mode="json")
    assert serialized["timestamp_kind"] == "OBSERVED_AT"
    assert serialized["timestamp"] == items[0].timestamp.isoformat().replace("+00:00", "Z")
