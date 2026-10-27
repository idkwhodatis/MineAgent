"""Offline explicit021 delta structure, not PostgreSQL execution."""

import pytest

from backend.app.rdagent_execution_links_sql import (
    TABLES,
    render_downgrade_sql,
    render_upgrade_sql,
)


def test_only_actual_final_generation_gate_opens():
    sql = render_upgrade_sql()
    assert (
        "DROP TRIGGER rd_source_phase_closed ON @SCHEMA@.factor_generation_outcomes;"
        in sql
    )
    for table in (
        "factor_evaluation_outcomes",
        "factor_cutoff_outcomes",
        "factor_research_checkpoints",
    ):
        assert "DROP TRIGGER rd_source_phase_closed ON @SCHEMA@." + table not in sql
    assert "factor_execution_evaluation_outcomes" in sql
    assert "research_process_states" in sql and "state.status='running'" in sql
    assert "charged.outcome<>'conservative_unknown'" in sql
    assert "charged.charge IS DISTINCT FROM held.amounts" in sql
    assert "DROP FUNCTION mineagent_neutral" not in sql


@pytest.mark.parametrize("table", TABLES)
def test_exact_deferred_participants_and_reversible_empty_only(table):
    assert f"ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED" in render_upgrade_sql()
    assert (
        f"DROP TRIGGER rd_execution_generation_closure ON @SCHEMA@.{table}"
        in render_downgrade_sql()
    )
    assert "Execution generation evidence prevents downgrade" in render_downgrade_sql()
    assert "DROP TABLE" not in render_downgrade_sql()
