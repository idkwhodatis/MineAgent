"""Offline literal/graph checks only; genuine PostgreSQL execution is separate."""

import ast
from pathlib import Path

from backend.app import native_storage_ddl


def literals():
    path = (
        Path(__file__).resolve().parents[2]
        / "backend/alembic/versions/019_native_hdf_storage.py"
    )
    tree = ast.parse(path.read_text())
    return {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and not isinstance(node.value, ast.BinOp)
    }, tree


def test_migration_has_frozen_sql_not_application_imports():
    values, tree = literals()
    assert values["revision"] == "019_native_hdf_storage"
    assert values["down_revision"] == "018_rd_source_persistence"
    for name in (
        "TABLE_SQL",
        "SEMANTIC_SQL",
        "CLAIM_SQL",
        "NORMALIZER_SQL",
        "CANONICAL_SQL",
    ):
        assert values[name] == native_storage_ddl.qualified_sql(
            getattr(native_storage_ddl, name)
        )
    assert all(
        not (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("backend")
        )
        for node in ast.walk(tree)
    )


def test_normalizer_consumer_has_immediate_unique_and_exact_foreign_keys():
    sql = native_storage_ddl.TABLE_SQL
    assert "CREATE TABLE factor_native_normalizer_inputs" in sql
    assert "UNIQUE (publication_id)" in sql
    assert (
        "REFERENCES factor_native_publications (owner_id, run_id, attempt_id, generation, id)"
        in sql
    )
    assert (
        "REFERENCES research_process_receipts (owner_id, run_id, attempt_id, generation, admission_id, id, kind)"
        in sql
    )


def test_native_semantics_do_not_treat_overdue_containment_as_result():
    sql = native_storage_ddl.SEMANTIC_SQL
    assert "deadline_bound_met' IS DISTINCT FROM 'true'::jsonb" in sql
    assert "not_after')::timestamptz+interval '1 second'" in sql
    assert "root_device'" in sql and "root_inode'" in sql


def test_native_sql_pins_installation_schema_and_preserves_literal_table_names():
    values, _ = literals()
    sql = values["SEMANTIC_SQL"]
    assert "FUNCTION @SCHEMA@.mineagent_native_check" in sql
    assert "FROM @SCHEMA@.factor_native_publications" in sql
    assert "SET search_path=pg_catalog" in sql
    assert "TG_TABLE_NAME='factor_native_publications'" in sql
    assert (
        "RETURN @SCHEMA@.mineagent_rd_canonical_json(v,true,depth)"
        in values["CANONICAL_SQL"]
    )
    assert "@SCHEMA@.mineagent_engine_object_claim" in values["PRIOR_CLAIM_SQL"]
    assert "native_hdf" not in values["PRIOR_CLAIM_SQL"]
