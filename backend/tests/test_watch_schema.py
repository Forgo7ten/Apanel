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


def test_initial_schema_is_the_single_alembic_head() -> None:
    versions_dir = Path(__file__).parents[1] / "alembic" / "versions"
    paths = sorted(versions_dir.glob("*.py"))
    assert [path.name for path in paths] == ["0001_initial_schema.py"]

    path = paths[0]
    spec = importlib.util.spec_from_file_location("apanel_0001_initial", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "0001_initial_schema"
    assert migration.down_revision is None


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
