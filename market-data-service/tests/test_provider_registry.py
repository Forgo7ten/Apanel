import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pytest

from app.core.config import Settings
from app.domain.market_data import DailyBar, Dividend, Quote, Security
from app.providers.base import MarketDataProvider
from app.providers.registry import (
    ProviderRegistry,
    UnknownProviderError,
    close_provider,
    create_provider,
)
from app.providers.tdx import TDXProvider
from app.providers.tdx_client import PytdxClient


@dataclass
class StubProvider(MarketDataProvider):
    async def get_symbols(self) -> Sequence[Security]:
        return ()

    async def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    async def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment="none",
    ) -> Sequence[DailyBar]:
        raise NotImplementedError

    async def get_dividends(self, symbol: str) -> Sequence[Dividend]:
        raise NotImplementedError


def test_registry_selects_replaceable_provider_by_configuration() -> None:
    registry = ProviderRegistry()
    registry.register("stub", lambda **_: StubProvider())

    provider = create_provider(
        Settings(market_data_provider="stub"),
        registry=registry,
    )

    assert isinstance(provider, StubProvider)
    assert registry.available() == ("stub",)


def test_registry_rejects_unknown_provider_name() -> None:
    with pytest.raises(UnknownProviderError, match="unknown"):
        ProviderRegistry().create("unknown")


def test_settings_exposes_provider_and_timeout_selection() -> None:
    settings = Settings(
        market_data_provider="tdx",
        provider_timeout_seconds=1.5,
        tdx_servers="first.test:7709,second.test:7709",
        tdx_retry_attempts=2,
        tdx_symbol_timeout_seconds=42,
    )

    assert settings.market_data_provider == "tdx"
    assert settings.provider_timeout_seconds == 1.5
    assert settings.tdx_servers == ("first.test:7709", "second.test:7709")
    assert settings.tdx_retry_attempts == 2
    assert settings.tdx_symbol_timeout_seconds == 42


def test_default_tdx_registry_builds_configured_real_client_without_connecting() -> None:
    provider = create_provider(
        Settings(
            market_data_provider="tdx",
            tdx_servers=("first.test:7709",),
            tdx_connect_timeout_seconds=0.5,
        )
    )

    assert isinstance(provider, TDXProvider)
    assert isinstance(provider._client, PytdxClient)
    assert provider._client.servers[0].host == "first.test"
    assert provider._client.servers[0].port == 7709
    assert provider._symbol_timeout_seconds == 60


def test_close_provider_runs_blocking_client_cleanup_off_loop() -> None:
    class ClosingProvider(StubProvider):
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    provider = ClosingProvider()
    asyncio.run(close_provider(provider))

    assert provider.closed
