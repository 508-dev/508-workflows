"""Regression coverage for reversible agent-memory schema changes."""

from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_memory_jsonb_alignment_migration() -> ModuleType:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "apps/worker/src/five08/worker/migrations/versions"
        / "20260804_0100_align_agent_memory_jsonb_limit.py"
    )
    spec = importlib.util.spec_from_file_location(
        "agent_memory_jsonb_alignment_migration", migration_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_jsonb_alignment_downgrade_keeps_newly_wide_rows() -> None:
    """Rollback restores the old guard without failing on upgraded data."""

    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration = _load_memory_jsonb_alignment_migration()
    migration.op = Operations(context)

    migration.downgrade()

    sql = output.getvalue()
    assert "DROP CONSTRAINT ck_agent_memory_facts_value_json_size" in sql
    assert "octet_length(value_json::text) <= 8192" in sql
    assert "NOT VALID" in sql
