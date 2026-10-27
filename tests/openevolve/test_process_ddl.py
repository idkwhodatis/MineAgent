"""SQL construction only; actual PostgreSQL/Docker acceptance remains required."""

import pytest

from backend.app.openevolve_process_ddl import render_sql
from runtimes.openevolve_evaluator.contracts import PROFILE


@pytest.mark.parametrize("schema", ["public", "oe024_test"])
def test_exact_profiles_and_checked_process_chain(schema):
    sql = render_sql(schema)
    assert "@" not in sql
    assert f"CREATE FUNCTION {schema}.mineagent_oe_process_valid" in sql
    assert sql.count("CREATE CONSTRAINT TRIGGER oe024_process_consistent") == 9
    assert sql.count("THEN rule:=") == 8
    for required in (
        PROFILE,
        "mineagent.openevolve-orchestrator.v1",
        "600000",
        "90000",
        "268500992",
        "mineagent_oe_process_payload(a.profile,p.kind,p.payload) IS NOT TRUE",
        "z.outcome='conservative_unknown' AND z.charge=b.amounts",
        "st.status='stopped') IS DISTINCT FROM (stopped IS NOT NULL)",
        "b.state='settled' AND stopped IS NULL",
        "st.status='launch_intended' AND (ident IS NOT NULL",
        "started_report_sha256",
        "child->'controller'->'host_pid'=ident->'leader'->'host_pid'",
        "bootstrap_sha256",
        "native_artifacts_sha256",
        "deadline_bound_met",
        "max_seconds:=(CASE WHEN controller THEN 600 ELSE 60 END)",
        "EXCEPTION WHEN OTHERS THEN RETURN false",
    ):
        assert required in sql
    assert "SECURITY DEFINER" not in sql and "DROP" not in sql


@pytest.mark.parametrize("schema", ["x;DROP TABLE y", "a.b", "Upper", "", None, True])
def test_schema_is_not_executable_input(schema):
    with pytest.raises(ValueError):
        render_sql(schema)
