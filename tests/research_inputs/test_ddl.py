"""Literal construction checks only; actual PostgreSQL gate remains required."""

import pytest

from backend.app.research_input_ddl import TABLES, render_sql


def test_all_five_tables_are_append_guarded_and_deferred():
    sql = render_sql()
    assert len(TABLES) == 5
    assert sql.count("CREATE CONSTRAINT TRIGGER") == 5
    assert sql.count("CREATE TRIGGER") == 5
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "mineagent_workflow_lock(o,w)" in sql
    assert "pg_try_advisory_xact_lock(k)" in sql


def test_feedback_requires_actual_stage_result_and_existing_release_membership():
    sql = render_sql()
    assert "experiment_feedback_projection" in sql
    assert "factor_direct_analysis_outcomes" in sql
    assert "research_workflow_checkpoints" in sql
    assert "data_usage_exposures" in sql
    assert ":factor:v1" in sql and ":label:v1" in sql
    assert "retained input rows cannot be deleted" in sql
    assert "pg_read_file" not in sql and "COPY " not in sql
    assert "CREATE EXTENSION" not in sql and "digest(" not in sql


def test_exact_output_and_canonical_source_closure():
    sql = render_sql()
    for field in (
        "raw_object_sha256",
        "mapping_sha256",
        "outputs_sha256",
        "content_sha256",
        "item_ordinal",
    ):
        assert field in sql
    assert "n=3 AND (previous_state<>'started'" in sql
    assert "terminal.state<>'succeeded' AND EXISTS" in sql
    assert "mineagent_rd_canonical_json" in sql
    assert "IF COALESCE((payload->>'schema_version'" in sql


@pytest.mark.parametrize("schema", ["public;DROP TABLE users", "a.b", "Mixed", ""])
def test_closed_schema_identifier(schema):
    with pytest.raises(ValueError):
        render_sql(schema)


def test_function_runtime_search_path_is_pinned():
    sql = render_sql("mineagent_test")
    assert sql.count("SET search_path TO mineagent_test, pg_catalog") == 3
