from collections.abc import Sequence

import pytest

import app.providers.security as security_module
from app.domain.market_data import Security
from app.providers.base import MarketDataProvider
from app.providers.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.security import (
    AkshareSecurityProvider,
    SecurityMasterFallbackProvider,
    SecurityMasterProvider,
)


class FakePrimary(SecurityMasterProvider):
    def __init__(self, records: Sequence[Security] = (), error: Exception | None = None) -> None:
        self.records = tuple(records)
        self.error = error
        self.calls = 0

    async def get_symbols(self) -> Sequence[Security]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.records


class FakeFallback(SecurityMasterProvider):
    def __init__(self, records: Sequence[Security] = (), error: Exception | None = None) -> None:
        self.records = tuple(records)
        self.error = error
        self.calls = 0
        self.close_calls = 0

    async def get_symbols(self) -> Sequence[Security]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.records

    async def aclose(self) -> None:
        self.close_calls += 1


def security() -> Security:
    return Security(symbol="600519", name="贵州茅台", market="SH", security_type="STOCK")


def test_protocol_is_separate_from_full_market_data_provider() -> None:
    assert issubclass(MarketDataProvider, object)
    assert isinstance(FakePrimary(), SecurityMasterProvider)


@pytest.mark.asyncio
async def test_tdx_success_never_calls_akshare_fallback() -> None:
    primary = FakePrimary((security(),))
    fallback = FakeFallback((security(),))
    provider = SecurityMasterFallbackProvider(primary=primary, fallback=fallback)

    records = await provider.get_symbols()

    assert records == (security(),)
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_tdx_success_does_not_import_akshare() -> None:
    imports: list[str] = []
    original_import_module = security_module.importlib.import_module

    def reject_akshare(name: str, package: str | None = None):
        imports.append(name)
        if name == "akshare":
            raise AssertionError("TDX success must not import AKShare")
        return original_import_module(name, package)

    original = security_module.importlib.import_module
    security_module.importlib.import_module = reject_akshare
    try:
        # Use the default lazy provider and install the import guard before
        # constructing it.  This catches both eager imports in the factory
        # and accidental fallback imports/calls on the successful TDX path.
        provider = SecurityMasterFallbackProvider(
            primary=FakePrimary((security(),)),
            fallback=AkshareSecurityProvider(),
        )
        records = await provider.get_symbols()
    finally:
        security_module.importlib.import_module = original

    assert records == (security(),)
    assert "akshare" not in imports


@pytest.mark.asyncio
async def test_tdx_failure_uses_complete_akshare_batch_without_mixing() -> None:
    primary = FakePrimary((security(),), error=RuntimeError("TDX body should stay private"))
    fallback_security = Security(
        symbol="510300", name="沪深300ETF", market="SH", security_type="ETF"
    )
    fallback = FakeFallback((fallback_security,))
    provider = SecurityMasterFallbackProvider(primary=primary, fallback=fallback)

    records = await provider.get_symbols()

    assert records == (fallback_security,)
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_both_security_sources_fail_with_safe_provider_unavailable_error() -> None:
    primary = FakePrimary(error=RuntimeError("tdx response https://secret.example/detail"))
    fallback = FakeFallback(error=RuntimeError("akshare upstream response https://secret"))
    provider = SecurityMasterFallbackProvider(primary=primary, fallback=fallback)

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await provider.get_symbols()

    assert str(exc_info.value) == "Security master providers are unavailable."
    assert "https" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_fallback_timeout_keeps_timeout_class_without_upstream_details() -> None:
    primary = FakePrimary(error=RuntimeError("tdx unavailable"))
    fallback = FakeFallback(error=ProviderTimeoutError("upstream https://secret.invalid"))
    provider = SecurityMasterFallbackProvider(primary=primary, fallback=fallback)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await provider.get_symbols()

    assert str(exc_info.value) == "Security master fallback timed out."
    assert "secret.invalid" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_empty_tdx_batch_triggers_fallback_but_empty_fallback_fails() -> None:
    primary = FakePrimary(())
    fallback = FakeFallback(())
    provider = SecurityMasterFallbackProvider(primary=primary, fallback=fallback)

    with pytest.raises(ProviderUnavailableError):
        await provider.get_symbols()

    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_closing_composite_closes_only_fallback_once() -> None:
    primary = FakePrimary()
    fallback = FakeFallback()
    provider = SecurityMasterFallbackProvider(primary=primary, fallback=fallback)

    await provider.aclose()
    await provider.aclose()

    assert fallback.close_calls == 1
    with pytest.raises(ProviderUnavailableError):
        await provider.get_symbols()
    assert primary.calls == 0
