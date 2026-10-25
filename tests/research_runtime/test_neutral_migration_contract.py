"""Offline regression contracts complement, not replace, actual PostgreSQL tests."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

PATH = (
    Path(__file__).resolve().parents[2]
    / "backend/alembic/versions/015_neutral_primitives.py"
)


def migration():
    spec = importlib.util.spec_from_file_location("neutral_migration_contract", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zero_argument_immutability_does_not_rely_on_nullable_trigger_arguments():
    source = PATH.read_text()
    assert "COALESCE(TG_ARGV, ARRAY[]::text[])" in source
    assert "TG_NARGS = 0 AND NEW IS DISTINCT FROM OLD" in source


def test_downgrade_preflights_isolation_and_all_fk_parents_before_destructive_ddl():
    module = migration()
    statements = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        module.downgrade()
    assert "transaction_isolation" in statements[0]
    assert "read committed" in statements[0]
    assert "pg_try_advisory_xact_lock(1413960260, 1129270340)" in statements[0]
    assert statements[1] == (
        "LOCK TABLE "
        + ", ".join((*module.TABLES, "users", "research_runs", "execution_attempts"))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    assert "Neutral evidence prevents destructive downgrade" in statements[2]
    assert all("DROP " not in sql for sql in statements[:3])
