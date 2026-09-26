"""Typed, provider-neutral market data records.

The market-data service treats prices as decimal values, trading dates as
timezone-free calendar dates, and intraday timestamps as timezone-aware UTC
instants.  Provider adapters are responsible for converting their raw values
into these records before they reach repositories or downstream services.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class InvalidMarketDataError(ValueError):
    """Raised when a provider record violates the domain data contract."""


class UnknownMarketError(InvalidMarketDataError):
    """Raised when a security market cannot be mapped to SH/SZ/BJ."""


class UnknownAdjustmentError(InvalidMarketDataError):
    """Raised for an unsupported price adjustment mode."""


class Market(StrEnum):
    """Supported mainland China exchange markets."""

    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"


class SecurityType(StrEnum):
    """Supported security categories in the v1 product scope."""

    STOCK = "STOCK"
    ETF = "ETF"


class Adjustment(StrEnum):
    """Price adjustment modes shared by bars and indicator calculations."""

    QFQ = "qfq"
    NONE = "none"


# Descriptive aliases keep callers from having to guess whether a value is an
# enum or a wire-level code while preserving one canonical implementation.
MarketCode = Market
AdjustmentType = Adjustment
SecurityCategory = SecurityType


_SYMBOL_PATTERN = re.compile(
    r"^(?:(?P<prefix>SH|SZ|BJ)[.:]?(?P<prefixed_code>\d{6})|"
    r"(?P<suffix_code>\d{6})[.:](?P<suffix>SH|SZ|BJ)|(?P<bare>\d{6}))$",
    re.IGNORECASE,
)
_MARKET_CODES = {market.value for market in Market}


def normalize_market(value: Market | str) -> Market:
    """Normalize a market code and reject unsupported exchanges."""

    if isinstance(value, Market):
        return value
    if not isinstance(value, str):
        raise UnknownMarketError(f"unknown market: {value!r}")
    normalized = value.strip().upper()
    if normalized not in _MARKET_CODES:
        raise UnknownMarketError(f"unknown market: {value!r}")
    return Market(normalized)


def normalize_security_type(value: SecurityType | str) -> SecurityType:
    """Normalize stock/ETF type names without accepting arbitrary values."""

    if isinstance(value, SecurityType):
        return value
    if not isinstance(value, str):
        raise InvalidMarketDataError(f"unknown security type: {value!r}")
    normalized = value.strip().upper()
    try:
        return SecurityType(normalized)
    except ValueError as exc:
        raise InvalidMarketDataError(f"unknown security type: {value!r}") from exc


def normalize_adjustment(value: Adjustment | str) -> Adjustment:
    """Normalize the only v1 adjustment modes: qfq and none."""

    if isinstance(value, Adjustment):
        return value
    if not isinstance(value, str):
        raise UnknownAdjustmentError(f"unknown adjustment: {value!r}")
    normalized = value.strip().lower()
    try:
        return Adjustment(normalized)
    except ValueError as exc:
        raise UnknownAdjustmentError(f"unknown adjustment: {value!r}") from exc


def infer_market(symbol: str) -> Market:
    """Infer an exchange from a bare A-share/ETF code.

    Explicit market prefixes are validated and returned directly.  Bare codes
    use the stable exchange prefixes used by the v1 security universe; codes
    outside those prefixes must carry an explicit market and are rejected.
    """

    code, explicit_market = _parse_symbol(symbol)
    if explicit_market is not None:
        return explicit_market
    # ``900xxx`` remains a Shanghai B-share family while ``92xxxx`` is the
    # current Beijing exchange family.  Check these prefixes before the broad
    # legacy prefixes so a bare symbol keeps one deterministic meaning.
    if code.startswith("900"):
        return Market.SH
    if code.startswith("92"):
        return Market.BJ
    if code.startswith(("4", "8")):
        return Market.BJ
    if code.startswith(("0", "1", "2", "3")):
        return Market.SZ
    if code.startswith(("5", "6")):
        return Market.SH
    raise UnknownMarketError(f"cannot infer market for symbol: {symbol!r}")


def normalize_symbol(value: str) -> str:
    """Return a six-digit canonical symbol without an exchange prefix."""

    code, _ = _parse_symbol(value)
    # ``_parse_symbol`` validates the shape and exchange prefix.  Inference is
    # intentionally performed for bare codes so unsupported prefixes are not
    # silently treated as a valid security.
    infer_market(value)
    return code


def _parse_symbol(value: str) -> tuple[str, Market | None]:
    if not isinstance(value, str):
        raise InvalidMarketDataError(f"symbol must be a string, got {type(value).__name__}")
    candidate = value.strip().upper()
    match = _SYMBOL_PATTERN.fullmatch(candidate)
    if match is None:
        if candidate[:2] in _MARKET_CODES:
            raise InvalidMarketDataError(f"invalid symbol format: {value!r}")
        if any(candidate.startswith(prefix) for prefix in ("SH", "SZ", "BJ")):
            raise UnknownMarketError(f"invalid market-qualified symbol: {value!r}")
        if "." in candidate or ":" in candidate:
            raise UnknownMarketError(f"unknown market-qualified symbol: {value!r}")
        raise InvalidMarketDataError(f"invalid symbol format: {value!r}")

    prefix = match.group("prefix")
    suffix = match.group("suffix")
    if prefix is not None:
        return match.group("prefixed_code"), normalize_market(prefix)
    if suffix is not None:
        return match.group("suffix_code"), normalize_market(suffix)
    return match.group("bare"), None


def _decimal(value: Decimal | int | float | str, field: str) -> Decimal:
    if isinstance(value, bool):
        raise InvalidMarketDataError(f"{field} must be a finite decimal")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidMarketDataError(f"{field} must be a finite decimal") from exc
    if not decimal_value.is_finite():
        raise InvalidMarketDataError(f"{field} must be a finite decimal")
    return decimal_value


def _calendar_date(value: date | str, field: str) -> date:
    if isinstance(value, datetime):
        raise InvalidMarketDataError(f"{field} must be a calendar date, not datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise InvalidMarketDataError(f"{field} must be an ISO calendar date") from exc
    raise InvalidMarketDataError(f"{field} must be a calendar date")


def _utc_datetime(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidMarketDataError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidMarketDataError(f"{field} must include timezone information")
    return value.astimezone(UTC)


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value, field)


def _validate_symbol_market(symbol: str, market: Market) -> str:
    normalized_symbol = normalize_symbol(symbol)
    inferred = infer_market(symbol)
    if inferred is not market:
        raise InvalidMarketDataError(
            f"symbol {normalized_symbol} belongs to {inferred.value}, not {market.value}"
        )
    return normalized_symbol


@dataclass(frozen=True, slots=True)
class Security:
    """Canonical security metadata for an A-share or ETF."""

    symbol: str
    name: str
    market: Market
    security_type: SecurityType
    exchange: str | None = None

    def __post_init__(self) -> None:
        market = normalize_market(self.market)
        symbol = _validate_symbol_market(self.symbol, market)
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidMarketDataError("security name must not be empty")
        security_type = normalize_security_type(self.security_type)
        exchange = market.value if self.exchange is None else str(self.exchange).strip().upper()
        if exchange not in _MARKET_CODES:
            raise UnknownMarketError(f"unknown exchange: {self.exchange!r}")
        if exchange != market.value:
            raise InvalidMarketDataError(
                f"exchange {exchange} does not match market {market.value}"
            )
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "security_type", security_type)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "name", self.name.strip())

    @property
    def type(self) -> SecurityType:
        """Compatibility view for wire payloads that call this ``type``."""

        return self.security_type


@dataclass(frozen=True, slots=True)
class Quote:
    """Intraday quote whose timestamp is normalized to UTC."""

    symbol: str
    price: Decimal | int | float | str
    timestamp: datetime
    change: Decimal | int | float | str | None = None
    change_percent: Decimal | int | float | str | None = None

    def __post_init__(self) -> None:
        symbol = normalize_symbol(self.symbol)
        price = _decimal(self.price, "price")
        if price <= 0:
            raise InvalidMarketDataError("price must be greater than zero")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "change", _optional_decimal(self.change, "change"))
        object.__setattr__(
            self,
            "change_percent",
            _optional_decimal(self.change_percent, "change_percent"),
        )
        object.__setattr__(self, "timestamp", _utc_datetime(self.timestamp, "timestamp"))

    @property
    def time(self) -> datetime:
        """Compatibility view for providers that name the timestamp ``time``."""

        return self.timestamp


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One OHLCV record under one explicit adjustment mode."""

    symbol: str
    trade_date: date | str
    open: Decimal | int | float | str
    high: Decimal | int | float | str
    low: Decimal | int | float | str
    close: Decimal | int | float | str
    volume: Decimal | int | float | str
    amount: Decimal | int | float | str | None = None
    adjustment: Adjustment | str = Adjustment.NONE

    def __post_init__(self) -> None:
        values = {
            field: _decimal(getattr(self, field), field)
            for field in ("open", "high", "low", "close", "volume")
        }
        amount = _optional_decimal(self.amount, "amount")
        if any(values[field] <= 0 for field in ("open", "high", "low", "close")):
            raise InvalidMarketDataError("OHLC prices must be greater than zero")
        if values["volume"] < 0:
            raise InvalidMarketDataError("volume must not be negative")
        if amount is not None and amount < 0:
            raise InvalidMarketDataError("amount must not be negative")
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise InvalidMarketDataError("high must be at least open, close, and low")
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise InvalidMarketDataError("low must be at most open, close, and high")
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "trade_date", _calendar_date(self.trade_date, "trade_date"))
        for field, value in values.items():
            object.__setattr__(self, field, value)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "adjustment", normalize_adjustment(self.adjustment))

    @property
    def date(self) -> date:
        """Compatibility view matching the storage column name."""

        return self.trade_date

    @property
    def adjust_type(self) -> Adjustment:
        """Compatibility view matching the storage column name."""

        return self.adjustment


@dataclass(frozen=True, slots=True)
class Dividend:
    """A cash dividend event, represented in currency units per security."""

    symbol: str
    date: date | str
    cash_amount: Decimal | int | float | str

    def __post_init__(self) -> None:
        amount = _decimal(self.cash_amount, "cash_amount")
        if amount < 0:
            raise InvalidMarketDataError("cash_amount must not be negative")
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "date", _calendar_date(self.date, "date"))
        object.__setattr__(self, "cash_amount", amount)

    @property
    def ex_date(self) -> date:
        """Compatibility view for providers that call the event date ``ex_date``."""

        return self.date
