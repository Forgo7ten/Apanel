"""Administrative command-line entry points."""

from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.errors import ApiError
from app.db.session import create_engine, dispose_engine
from app.services.auth_service import bootstrap_admin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Create the first administrator")
    bootstrap.add_argument("--username")
    bootstrap.add_argument("--email")
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "bootstrap-admin":
        return 2
    try:
        return asyncio.run(_bootstrap_admin(args))
    except ApiError as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
