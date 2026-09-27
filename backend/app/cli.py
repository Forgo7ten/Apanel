"""Administrative command-line entry points."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import inspect
import math
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.clients.market_data_hub import MarketDataHubClient, MarketDataHubClientError
from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import create_engine, dispose_engine
from app.models import UserSetting
from app.security.webhook_url import validate_feishu_webhook_url
from app.services.auth_service import bootstrap_admin
from app.services.user_secret_service import UserSecretService, audit_user_secrets


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a number greater than zero") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Create the first administrator")
    bootstrap.add_argument("--username")
    bootstrap.add_argument("--email")
    securities = commands.add_parser(
        "bootstrap-securities",
        help="Synchronize security metadata from the market-data provider",
    )
    securities.add_argument("--retry-until-success", action="store_true")
    securities.add_argument("--retry-interval-seconds", type=_positive_float, default=30.0)
    securities.add_argument("--timeout-seconds", type=_positive_float, default=10.0)
    commands.add_parser(
        "migrate-user-secrets",
        help="Encrypt legacy notification secrets and remove plaintext settings",
    )
    commands.add_parser(
        "audit-user-secrets",
        help="Verify whether legacy plaintext notification secrets remain",
    )
    return parser


async def _bootstrap_admin(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.validate_database_credentials()
    username = args.username or input("Admin username: ").strip()
    email = args.email or input("Admin email: ").strip()
    password = getpass.getpass("Admin password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not username or not email:
        raise SystemExit("Username and email are required.")
    if len(password) < 8:
        raise SystemExit("Password must contain at least 8 characters.")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    engine = create_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            user = await bootstrap_admin(
                session,
                username=username,
                email=email,
                password=password,
            )
    finally:
        await dispose_engine(engine)
    print(f"Created administrator {user.username} (id={user.id}).")
    return 0


class BootstrapAttemptStatus(Enum):
    """Classify one security synchronization attempt for retry policy."""

    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True, slots=True)
class BootstrapAttemptResult:
    """Safe, structured result of one security synchronization attempt."""

    status: BootstrapAttemptStatus
    message: str = ""
    count: int = 0


def _failure_result(message: str, *, retryable: bool) -> BootstrapAttemptResult:
    return BootstrapAttemptResult(
        status=(
            BootstrapAttemptStatus.RETRYABLE_FAILURE
            if retryable
            else BootstrapAttemptStatus.PERMANENT_FAILURE
        ),
        message=message,
    )


def _safe_market_data_error(error: MarketDataHubClientError) -> str:
    if error.retryable:
        return "market data request temporarily unavailable"
    return "market data synchronization failed"


def _bootstrap_result(data: object) -> BootstrapAttemptResult:
    if not isinstance(data, Mapping):
        return _failure_result(
            "market data hub returned an invalid synchronization summary",
            retryable=False,
        )
    if "success" in data:
        if data.get("success") is not True:
            return _failure_result(
                "market data hub reported synchronization failed",
                retryable=False,
            )
        data = data.get("data")
        if not isinstance(data, Mapping):
            return _failure_result(
                "market data hub returned an invalid synchronization summary",
                retryable=False,
            )
    if "ok" in data and data.get("ok") is not True:
        return _failure_result(
            "market data hub reported synchronization failed",
            retryable=False,
        )
    succeeded = data.get("succeeded")
    failed = data.get("failed")
    if (
        not isinstance(succeeded, int)
        or isinstance(succeeded, bool)
        or not isinstance(failed, int)
        or isinstance(failed, bool)
        or succeeded < 0
        or failed < 0
    ):
        return _failure_result(
            "market data hub returned an invalid synchronization summary",
            retryable=False,
        )
    if "total" in data:
        total = data.get("total")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or total != succeeded + failed
        ):
            return _failure_result(
                "market data hub returned an invalid synchronization summary",
                retryable=False,
            )
    if failed:
        return _failure_result(
            f"security synchronization incomplete: {failed} failed",
            retryable=False,
        )
    if succeeded == 0:
        return _failure_result("security synchronization returned no securities", retryable=True)
    return BootstrapAttemptResult(
        status=BootstrapAttemptStatus.SUCCESS,
        count=succeeded,
    )


async def _close_market_data_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def run_security_bootstrap(
    args: argparse.Namespace,
    *,
    settings: Any,
    market_data_client_factory: Callable[..., Any],
    sleep: Callable[[float], Awaitable[None]],
) -> int:
    """Run the non-interactive security bootstrap command."""

    client: Any | None = None
    attempt = 0
    exit_code = 1
    try:
        try:
            client = market_data_client_factory(
                settings.market_data_hub_url,
                internal_api_token=settings.internal_api_token,
                timeout_seconds=args.timeout_seconds,
            )
        except Exception:
            print(
                "Security bootstrap failed: could not create the market data client.",
                file=sys.stderr,
            )
            return exit_code

        while True:
            attempt += 1
            try:
                data = await client.sync_securities()
                result = _bootstrap_result(data)
            except asyncio.CancelledError:
                raise
            except MarketDataHubClientError as exc:
                result = _failure_result(
                    _safe_market_data_error(exc),
                    retryable=exc.retryable,
                )
            except Exception:
                result = _failure_result("market data client failed", retryable=False)

            if result.status is BootstrapAttemptStatus.SUCCESS:
                print(f"Security bootstrap completed: {result.count} securities synchronized.")
                exit_code = 0
                break

            print(
                f"Security bootstrap attempt {attempt} failed: {result.message}.",
                file=sys.stderr,
            )
            if (
                not args.retry_until_success
                or result.status is not BootstrapAttemptStatus.RETRYABLE_FAILURE
            ):
                break
            try:
                await sleep(args.retry_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                print(
                    "Security bootstrap failed: retry delay could not be completed.",
                    file=sys.stderr,
                )
                break
    finally:
        if client is not None:
            try:
                await _close_market_data_client(client)
            except asyncio.CancelledError:
                raise
            except Exception:
                print(
                    "Security bootstrap failed while closing the market data client.",
                    file=sys.stderr,
                )
                exit_code = 1
    return exit_code


async def _migrate_user_secrets() -> int:
    settings = get_settings()
    settings.validate_database_credentials()
    engine = create_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    migrated = 0
    invalid = 0
    try:
        async with session_factory() as session:
            rows = list((await session.execute(select(UserSetting))).scalars())
            secret_service = UserSecretService(session)
            allowed = tuple(
                host.strip()
                for host in settings.feishu_webhook_allowed_hosts.split(",")
                if host.strip()
            )
            for row in rows:
                payload = dict(row.settings or {})
                nested = payload.get("notification_settings")
                if not isinstance(nested, dict):
                    nested = payload.get("notification")
                raw = nested.get("feishu_webhook") if isinstance(nested, dict) else None
                if not isinstance(raw, str) or not raw.strip():
                    continue
                try:
                    webhook = validate_feishu_webhook_url(raw, allowed_hosts=allowed)
                except ValueError:
                    invalid += 1
                    continue
                await secret_service.set_feishu_webhook(row.user_id, webhook)
                for key in ("notification_settings", "notification"):
                    value = payload.get(key)
                    if isinstance(value, dict):
                        value = dict(value)
                        value.pop("feishu_webhook", None)
                        payload[key] = value
                payload.pop("feishu_webhook", None)
                row.settings = payload
                migrated += 1
            await session.commit()
    finally:
        await dispose_engine(engine)
    print(f"User secret migration completed: migrated={migrated} invalid={invalid}.")
    return 0 if invalid == 0 else 2


async def _audit_user_secrets() -> int:
    settings = get_settings()
    settings.validate_database_credentials()
    engine = create_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            audit = await audit_user_secrets(session)
    finally:
        await dispose_engine(engine)
    print(
        "User secret audit completed: "
        f"legacy_plaintext={audit.legacy_plaintext_count} "
        f"encrypted={audit.encrypted_count} "
        f"legacy_without_encrypted={audit.legacy_without_encrypted_count} "
        f"release_b_ready={'yes' if audit.release_b_ready else 'no'}."
    )
    return 0 if audit.release_b_ready else 2


def main(
    argv: list[str] | None = None,
    *,
    settings: Any | None = None,
    market_data_client_factory: Callable[..., Any] = MarketDataHubClient,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "bootstrap-admin":
            return asyncio.run(_bootstrap_admin(args))
        if args.command == "migrate-user-secrets":
            return asyncio.run(_migrate_user_secrets())
        if args.command == "audit-user-secrets":
            return asyncio.run(_audit_user_secrets())
        if args.command == "bootstrap-securities":
            return asyncio.run(
                run_security_bootstrap(
                    args,
                    settings=settings or get_settings(),
                    market_data_client_factory=market_data_client_factory,
                    sleep=sleep,
                )
            )
        return 2
    except ApiError as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
