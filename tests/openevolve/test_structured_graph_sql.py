"""Construction/producer agreement only; PostgreSQL gates remain required."""

from backend.app import oe_structured_graph_sql as graph
from backend.app.oe_structured_engine_process import PROCESS_AMOUNT


def test_native_pending_admission_has_exact_execution_input_and_full_original_hold():
    sql = graph.render_upgrade_sql()
    for key, value in PROCESS_AMOUNT.model_dump().items():
        assert f"'{key}',{value}" in sql
    assert "e.program_version_id" in sql
    assert "li.payload->'inputs' IS DISTINCT FROM expected" in sql
    assert (
        "a.input_sha256 IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(expected)"
        in sql
    )
    assert "a.input_sha256 IS DISTINCT FROM e.strategy_input_sha256" not in sql
    assert "structured-factor-execution:" in sql
    assert "logical_operation_id=e.id)<>1" in sql


def test_each_existing_prefix_is_checked_without_requiring_future_rows():
    sql = graph.render_upgrade_sql()
    for table, predicate in graph.CHECKS:
        assert f"FROM @SCHEMA@.{table} WHERE run_id=rid" in sql
        assert f"mineagent_oes_{predicate}_check(item.id)" in sql
    boundary = sql.index(
        "IF r.status IS DISTINCT FROM 'succeeded' THEN RETURN; END IF;"
    )
    assert sql.index("Orphan structured model operation/transport/exchange") < boundary
    assert sql.index("<>maximum+2") > boundary
    assert "@CHECKS@" not in sql
    assert "CREATE TRIGGER" not in sql


def test_success_requires_positive_completion_and_no_unconsumed_work_or_refund():
    sql = graph.render_upgrade_sql()
    for clause in (
        "mineagent_oes_controller_completed(e.id) IS NOT TRUE",
        "<>2*maximum+1",
        "<>maximum+1",
        "<>maximum+2",
        "w.status='evaluated' AND w.candidate_id=c.id",
        "o.admission_id=a.id",
        "x.provider_transport_id=m.id",
        "paid.charge=b.amounts AND paid.outcome='conservative_unknown'",
        "o.measurement_canonical::jsonb->'eligible'='true'::jsonb",
    ):
        assert clause in sql
    # Worker errors legitimately permit fewer candidates than mutation slots.
    assert "oe_structured_candidates WHERE epoch_id=e.id)<>maximum+1" not in sql


def test_schema_is_literal_placeholder_and_downgrade_is_reverse_order():
    sql = graph.render_upgrade_sql()
    assert "SET search_path=pg_catalog" in sql
    assert "@SCHEMA@" in sql
    assert graph.render_downgrade_sql().index(
        "graph_check"
    ) < graph.render_downgrade_sql().index("native_check")
