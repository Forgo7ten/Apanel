import asyncio
from collections.abc import Sequence
from unittest.mock import patch

from app.core.config import Settings
from app.domain.market_data import Security
from app.providers.akshare import AkshareSecurityProvider, SecurityMasterFallbackProvider
from app.providers.eltdx import EltdxProvider
from app.providers.registry import close_provider, create_provider, create_security_master_provider


class FakeTdxClient:
    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakePrimary:
    async def get_symbols(self) -> Sequence[Security]:
        return ()


class ClosingProvider:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_create_provider_builds_eltdx_with_explicit_hosts() -> None:
    settings = Settings(
        eltdx_hosts="first.test:7709,second.test:7709",
        eltdx_timeout_seconds=1.5,
        eltdx_probe_hosts=False,
    )
    fake_client = FakeTdxClient()
    with patch("eltdx.TdxClient", return_value=fake_client) as factory:
        provider = create_provider(settings)
    assert isinstance(provider, EltdxProvider)
    factory.assert_called_once()
    kwargs = factory.call_args.kwargs
    assert kwargs["hosts"] == ["first.test:7709", "second.test:7709"]
    assert kwargs["timeout"] == 1.5
    assert kwargs["probe_hosts"] is False


def test_security_master_factory_builds_lazy_akshare_fallback() -> None:
    primary = FakePrimary()
    provider = create_security_master_provider(
        Settings(
            security_master_fallback_provider="akshare",
            akshare_security_timeout_seconds=8,
            akshare_min_stock_count=12,
            akshare_min_etf_count=2,
        ),
        primary=primary,
    )
    assert isinstance(provider, SecurityMasterFallbackProvider)
    assert isinstance(provider._fallback, AkshareSecurityProvider)
    assert provider._fallback._timeout_seconds == 8


def test_security_master_factory_can_disable_fallback() -> None:
    primary = FakePrimary()
    provider = create_security_master_provider(
        Settings(security_master_fallback_provider="none"),
        primary=primary,
    )
    assert provider is primary


def test_close_provider_runs_blocking_cleanup_off_loop() -> None:
    provider = ClosingProvider()
    asyncio.run(close_provider(provider))
    assert provider.closed
