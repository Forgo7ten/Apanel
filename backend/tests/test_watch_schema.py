"""Schema and migration contract checks for user-owned watch data."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.db.base import Base


def test_all_alembic_revision_ids_fit_version_table_column() -> None:
    versions_dir = Path(__file__).parents[1] / "alembic" / "versions"

    for path in sorted(versions_dir.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"apanel_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        assert len(migration.revision) <= 32, (
            f"{path.name} revision exceeds alembic_version.version_num varchar(32)"
        )


def test_alembic_revisions_form_one_complete_linear_chain() -> None:
    versions_dir = Path(__file__).parents[1] / "alembic" / "versions"
    paths = sorted(versions_dir.glob("*.py"))
    revisions: dict[str, str | None] = {}
    for path in paths:
        spec = importlib.util.spec_from_file_location(f"apanel_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        revisions[migration.revision] = migration.down_revision

    assert revisions["0001_initial_schema"] is None
    roots = {revision for revision, parent in revisions.items() if parent is None}
    referenced = {parent for parent in revisions.values() if parent is not None}
    heads = set(revisions) - referenced
    assert roots == {"0001_initial_schema"}
    assert len(heads) == 1
    assert all(parent is None or parent in revisions for parent in revisions.values())

    seen: set[str] = set()
    current = next(iter(heads))
    while current is not None:
        assert current not in seen, "Alembic revision chain contains a cycle"
        seen.add(current)
        current = revisions[current]
    assert seen == set(revisions)


def test_watch_schema_has_owner_bound_relations_and_uniqueness() -> None:
    watch_tables = Base.metadata.tables["watch_tables"]
    symbols = Base.metadata.tables["watch_table_symbols"]
    columns = Base.metadata.tables["table_columns"]
    settings = Base.metadata.tables["user_settings"]

    assert watch_tables.c.user_id.nullable is False
    assert symbols.c.watch_table_id.nullable is False
    assert symbols.c.security_id.nullable is False
    assert columns.c.watch_table_id.nullable is False
    assert settings.c.user_id.nullable is False
    assert any(
        constraint.name == "uq_watch_table_symbols_table_security"
        for constraint in symbols.constraints
    )
    assert any(
        constraint.name == "uq_user_settings_user_id" for constraint in settings.constraints
    )
