"""Blocking, reconnecting adapter for the public TDX HQ protocol.

The application-facing provider is asynchronous, while ``pytdx`` exposes a
blocking socket client.  This module keeps that dependency behind a tiny
blocking protocol and creates one low-level connection per operation.  A
connection is therefore always closed in the same worker thread that used it,
including failed setup and failover attempts.

``pytdx``'s standard ``get_security_bars`` endpoint returns raw, unadjusted
bars.  It has no qfq argument.  The adapter consequently rejects qfq unless a
caller supplies an explicit factor transformer; it never labels raw bars as
qfq.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.config import DEFAULT_TDX_SERVERS
from app.domain.market_data import (
    Adjustment,
    Market,
    infer_market,
    normalize_adjustment,
    normalize_symbol,
)

from .errors import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUnsupportedError,
)

TDX_DAILY_CATEGORY = 9
TDX_MARKET_BY_DOMAIN = {
    Market.SZ: 0,
    Market.SH: 1,
    Market.BJ: 2,
}
DOMAIN_MARKET_BY_TDX = {value: key for key, value in TDX_MARKET_BY_DOMAIN.items()}


@dataclass(frozen=True, slots=True)
class TDXServer:
    """One TDX HQ TCP endpoint."""

    host: str
    port: int = 7709

    def __post_init__(self) -> None:
        host = self.host.strip()
        if not host:
            raise ValueError("TDX server host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("TDX server port must be between 1 and 65535")
        object.__setattr__(self, "host", host)

    @classmethod
    def parse(cls, value: str | Sequence[Any] | TDXServer) -> TDXServer:
        """Parse ``host:port`` or ``(host, port)`` configuration values."""

        if isinstance(value, cls):
            return value
        if isinstance(value, (tuple, list)):
            if len(value) != 2:
                raise ValueError(f"TDX server tuple must contain host and port: {value!r}")
            return cls(str(value[0]), int(value[1]))
        if not isinstance(value, str):
            raise TypeError(f"TDX server must be host:port text: {value!r}")
        candidate = value.strip()
        if not candidate:
            raise ValueError("TDX server must not be empty")
        if candidate.startswith("["):
            host, separator, port = candidate.rpartition("]:")
            if not separator:
                return cls(candidate.strip("[]"))
            return cls(host[1:], int(port))
        if ":" not in candidate:
            return cls(candidate)
        host, port = candidate.rsplit(":", 1)
        return cls(host, int(port))


TDXClientFactory = Callable[[], Any]
QFQTransformer = Callable[[str, Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]]


class _TDXOperationError(RuntimeError):
    """Internal marker for false/empty low-level responses."""


class PytdxClient:
    """Adapt ``pytdx.hq.TdxHq_API`` to the provider's blocking protocol.

    The default factory is imported lazily so unit tests and deployments that
    only use an injected fake do not import third-party code.  ``servers`` is
    ordered and each retry round tries every endpoint, which gives deterministic
    failover while still allowing a transient server to recover on the next
    round.
    """

    def __init__(
        self,
        servers: Sequence[str | Sequence[Any] | TDXServer] | None = DEFAULT_TDX_SERVERS,
        *,
        timeout_seconds: float = 5.0,
        retry_attempts: int = 1,
        symbol_max_pages: int = 100,
        bar_page_size: int = 800,
        bar_max_pages: int = 64,
        client_factory: TDXClientFactory | None = None,
        qfq_transformer: QFQTransformer | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if retry_attempts < 0:
            raise ValueError("retry_attempts must not be negative")
        if symbol_max_pages <= 0:
            raise ValueError("symbol_max_pages must be greater than zero")
        if not 1 <= bar_page_size <= 800:
            raise ValueError("bar_page_size must be between 1 and 800")
        if bar_max_pages <= 0:
            raise ValueError("bar_max_pages must be greater than zero")
        selected_servers = DEFAULT_TDX_SERVERS if servers is None else servers
        parsed_servers = tuple(TDXServer.parse(server) for server in selected_servers)
        if not parsed_servers:
            raise ValueError("at least one TDX server is required")
        self._servers = parsed_servers
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = retry_attempts
        self._symbol_max_pages = symbol_max_pages
        self._bar_page_size = bar_page_size
        self._bar_max_pages = bar_max_pages
        self._client_factory = client_factory or _default_pytdx_factory
        self._qfq_transformer = qfq_transformer

    @property
    def servers(self) -> tuple[TDXServer, ...]:
        """Configured endpoints, useful for diagnostics without secrets."""

        return self._servers

    def fetch_symbols(self) -> Sequence[Mapping[str, Any]]:
        """Fetch and page security lists for SZ, SH, and BJ markets."""

        def operation(client: Any) -> list[Mapping[str, Any]]:
            records: list[Mapping[str, Any]] = []
            for market_code, market in DOMAIN_MARKET_BY_TDX.items():
                count_method = getattr(client, "get_security_count", None)
                count = None if count_method is None else count_method(market_code)
                if count_method is not None and count is None:
                    raise _TDXOperationError(f"TDX returned no security count for market {market}")
                if count is not None and int(count) <= 0:
                    continue
                offset = 0
                for _ in range(self._symbol_max_pages):
                    page = client.get_security_list(market_code, offset)
                    if page is None:
                        raise _TDXOperationError(
                            f"TDX returned no security page for market {market} at {offset}"
                        )
                    page_records = tuple(page)
                    if not page_records:
                        break
                    for record in page_records:
                        if not isinstance(record, Mapping):
                            raise _TDXOperationError(
                                "TDX security list contains a non-mapping record"
                            )
                        enriched = dict(record)
                        enriched.setdefault("market", market.value)
                        records.append(enriched)
                    offset += len(page_records)
                    if count is not None and offset >= int(count):
                        break
                else:
                    raise _TDXOperationError(
                        f"TDX security list exceeded {self._symbol_max_pages} pages for {market}"
                    )
            return records

        return self._with_failover("symbols", operation)

    def fetch_quote(self, symbol: str) -> Mapping[str, Any]:
        """Fetch one quote and normalize TDX's market/code response shape."""

        canonical_symbol = normalize_symbol(symbol)
        market = infer_market(canonical_symbol)
        market_code = TDX_MARKET_BY_DOMAIN[market]

        def operation(client: Any) -> Mapping[str, Any]:
            result = client.get_security_quotes([(market_code, canonical_symbol)])
            if result is None:
                raise _TDXOperationError("TDX returned no quote")
            if isinstance(result, Mapping):
                raw = result
            else:
                rows = tuple(result)
                if not rows:
                    raise _TDXOperationError("TDX returned an empty quote response")
                raw = rows[0]
            if not isinstance(raw, Mapping):
                raise _TDXOperationError("TDX quote response is not a mapping")
            record = dict(raw)
            record["symbol"] = str(record.get("code", canonical_symbol))
            record["market"] = market.value
            if "timestamp" not in record:
                record["timestamp"] = _quote_timestamp(record)
            if "change" not in record and record.get("last_close") is not None:
                record["change"] = _difference(record.get("price"), record.get("last_close"))
            if "change_percent" not in record and record.get("last_close"):
                change = _difference(record.get("price"), record.get("last_close"))
                record["change_percent"] = _ratio_percent(change, record.get("last_close"))
            return record

        return self._with_failover("quote", operation)

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjustment: Adjustment,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch raw daily bars, or use an explicitly supplied qfq seam."""

        canonical_symbol = normalize_symbol(symbol)
        selected_adjustment = normalize_adjustment(adjustment)
        if selected_adjustment is Adjustment.QFQ and self._qfq_transformer is None:
            raise ProviderUnsupportedError(
                "TDX get_security_bars returns unadjusted bars; qfq requires a configured "
                "qfq_transformer/factor provider"
            )
        if start > end:
            raise ValueError("start must not be after end")
        market_code = TDX_MARKET_BY_DOMAIN[infer_market(canonical_symbol)]

        def operation(client: Any) -> list[Mapping[str, Any]]:
            raw_records: list[Mapping[str, Any]] = []
            offset = 0
            for _ in range(self._bar_max_pages):
                page = client.get_security_bars(
                    TDX_DAILY_CATEGORY,
                    market_code,
                    canonical_symbol,
                    offset,
                    self._bar_page_size,
                )
                if page is None:
                    raise _TDXOperationError("TDX returned no daily-bar page")
                page_records = tuple(page)
                if not page_records:
                    break
                for record in page_records:
                    if not isinstance(record, Mapping):
                        raise _TDXOperationError("TDX daily bars contain a non-mapping record")
                    normalized = _normalize_bar_record(record, canonical_symbol)
                    if start <= normalized["trade_date"] <= end:
                        raw_records.append(normalized)
                offset += len(page_records)
                if len(page_records) < self._bar_page_size:
                    break
            else:
                raise _TDXOperationError(
                    f"TDX daily bars exceeded {self._bar_max_pages} pages for {canonical_symbol}"
                )
            return _deduplicate_bars(raw_records)

        records = self._with_failover("daily bars", operation)
        if selected_adjustment is Adjustment.NONE:
            return tuple({**record, "adjustment": Adjustment.NONE.value} for record in records)

        transformer = self._qfq_transformer
        if transformer is None:  # pragma: no cover - guarded above, keeps the seam explicit
            raise ProviderUnsupportedError("qfq transformer is not configured")
        transformed = transformer(canonical_symbol, records)
        result: list[Mapping[str, Any]] = []
        for record in transformed:
            if not isinstance(record, Mapping):
                raise ProviderUnsupportedError("qfq transformer returned a non-mapping record")
            normalized = dict(record)
            if normalize_adjustment(normalized.get("adjustment")) is not Adjustment.QFQ:
                raise ProviderUnsupportedError(
                    "qfq transformer must explicitly mark every bar adjustment='qfq'"
                )
            result.append(normalized)
        return tuple(result)

    def fetch_dividends(self, symbol: str) -> Sequence[Mapping[str, Any]]:
        """Map TDX category-1 ex-rights rows to cash dividend events."""

        canonical_symbol = normalize_symbol(symbol)
        market_code = TDX_MARKET_BY_DOMAIN[infer_market(canonical_symbol)]

        def operation(client: Any) -> list[Mapping[str, Any]]:
            method = getattr(client, "get_xdxr_info", None)
            if method is None:
                return []
            rows = method(market_code, canonical_symbol)
            if rows is None:
                raise _TDXOperationError("TDX returned no dividend response")
            result: list[Mapping[str, Any]] = []
            for row in rows:
                if not isinstance(row, Mapping) or row.get("fenhong") is None:
                    continue
                result.append(
                    {
                        "symbol": canonical_symbol,
                        "date": date(
                            int(row["year"]),
                            int(row["month"]),
                            int(row["day"]),
                        ),
                        "cash_amount": row["fenhong"],
                    }
                )
            return result

        return self._with_failover("dividends", operation)

    def close(self) -> None:
        """Compatibility no-op; each operation owns and closes its client."""

    def _with_failover(self, operation_name: str, operation: Callable[[Any], Any]) -> Any:
        errors: list[Exception] = []
        for _round in range(self._retry_attempts + 1):
            for server in self._servers:
                client: Any = None
                try:
                    client = self._client_factory()
                    connected = _connect(client, server, self._timeout_seconds)
                    if connected is False or connected is None:
                        raise _TDXOperationError(
                            f"could not connect to {server.host}:{server.port}"
                        )
                    result = operation(client)
                    return result
                except ProviderUnsupportedError:
                    raise
                except ProviderConfigurationError:
                    raise
                except Exception as exc:  # low-level clients may use socket/OSError classes
                    errors.append(exc)
                finally:
                    _close_client(client)

        if errors and all(
            isinstance(error, (TimeoutError, ProviderTimeoutError)) for error in errors
        ):
            raise ProviderTimeoutError(
                f"TDX {operation_name} timed out after trying {len(errors)} connection(s)"
            ) from errors[-1]
        if errors:
            raise ProviderUnavailableError(
                f"TDX {operation_name} failed after {len(errors)} connection attempt(s)"
            ) from errors[-1]
        raise ProviderUnavailableError(f"TDX {operation_name} failed without an attempt")


# Names kept explicit for callers that prefer the provider-neutral spelling.
TdxHqClient = PytdxClient
TDXClient = PytdxClient


def _default_pytdx_factory() -> Any:
    try:
        from pytdx.hq import TdxHq_API
    except ImportError as exc:  # pragma: no cover - depends on deployment packaging
        raise ProviderConfigurationError(
            "pytdx is required for the default TDX client; install pytdx==1.72 "
            "or inject a TDX client"
        ) from exc
    return TdxHq_API(multithread=True, raise_exception=True)


def _connect(client: Any, server: TDXServer, timeout_seconds: float) -> Any:
    connect = getattr(client, "connect", None)
    if connect is None:
        raise ProviderConfigurationError("TDX client does not provide connect()")
    try:
        return connect(server.host, server.port, time_out=timeout_seconds)
    except TypeError as exc:
        # A small test double may use the conventional ``timeout`` spelling.
        if "time_out" not in str(exc):
            raise
        return connect(server.host, server.port, timeout=timeout_seconds)


def _close_client(client: Any) -> None:
    if client is None:
        return
    close = getattr(client, "close", None) or getattr(client, "disconnect", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        # The original operation determines the result; cleanup is best effort.
        return


def _normalize_bar_record(record: Mapping[str, Any], symbol: str) -> dict[str, Any]:
    value = record.get("trade_date", record.get("date", record.get("datetime")))
    trade_date = _parse_date(value)
    normalized = dict(record)
    normalized["symbol"] = str(record.get("symbol", record.get("code", symbol)))
    normalized["trade_date"] = trade_date
    normalized["open"] = record.get("open")
    normalized["high"] = record.get("high")
    normalized["low"] = record.get("low")
    normalized["close"] = record.get("close")
    normalized["volume"] = record.get("volume", record.get("vol"))
    normalized["amount"] = record.get("amount", record.get("turnover"))
    return normalized


def _deduplicate_bars(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    unique: dict[date, Mapping[str, Any]] = {}
    for record in records:
        unique[_parse_date(record["trade_date"])] = record
    return [unique[key] for key in sorted(unique)]


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip().replace("/", "-")
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError as exc:
            raise _TDXOperationError(f"invalid TDX bar date: {value!r}") from exc
    raise _TDXOperationError("TDX bar is missing a date")


def _quote_timestamp(record: Mapping[str, Any]) -> datetime:
    for key in ("datetime", "servertime", "time"):
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return value.replace(tzinfo=_shanghai_timezone())
            return value
        if isinstance(value, str):
            candidate = value.strip().replace("/", "-")
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                digits = re.sub(r"\D", "", candidate)
                if len(digits) >= 14:
                    try:
                        parsed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
                    except ValueError:
                        continue
                else:
                    try:
                        parsed_time = time.fromisoformat(candidate)
                    except ValueError:
                        continue
                    parsed = datetime.combine(date.today(), parsed_time)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=_shanghai_timezone())
            return parsed
    # This is the observation instant, not an invented exchange timestamp.
    return datetime.now(UTC)


def _shanghai_timezone() -> timezone:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:  # pragma: no cover - minimal containers may lack tzdata
        return timezone(timedelta(hours=8))


def _difference(left: Any, right: Any) -> Decimal | None:
    if left is None or right is None:
        return None
    return Decimal(str(left)) - Decimal(str(right))


def _ratio_percent(numerator: Decimal | None, denominator: Any) -> Decimal | None:
    if numerator is None or denominator in (None, 0, "0"):
        return None
    return numerator / Decimal(str(denominator)) * 100
