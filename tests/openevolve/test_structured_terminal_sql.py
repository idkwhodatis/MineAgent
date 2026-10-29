"""Construction-only terminal graph assertions, not physical or PostgreSQL proof."""

from backend.app import oe_structured_terminal_sql as terminal


def test_worker_error_does_not_fabricate_numeric_measurement():
    sql = terminal.render_upgrade_sql()
    assert "w.status='worker_error'" in sql
    assert "(NULL::uuid,NULL::uuid,NULL::uuid)" in sql
    assert "j->'observation'->'child' IS DISTINCT FROM 'null'::jsonb" in sql
    assert (
        "j->'projection'->>'objective_ack_id' IS DISTINCT FROM w.engine_outcome_id::text"
        in sql
    )
    assert "mineagent_oes_outcome_check(o.id)" in sql


def test_population_requires_actual_result_metrics_and_fixed_artifact():
    sql = terminal.render_upgrade_sql()
    assert "jsonb_build_object('structured_feedback',o.measurement_canonical)" in sql
    assert "program->'metrics' IS DISTINCT FROM metrics" in sql
    assert "c.ordinal>control.frontier" in sql
    assert "@PROGRAM_KEYS@" not in sql
    assert "@METADATA_KEYS@" not in sql
    assert "jsonb_object_keys(program)" in sql


def test_error_rejected_state_unchanged_and_mutation_full_charge():
    sql = terminal.render_upgrade_sql()
    assert "t.outcome IN ('rejected','error')" in sql
    assert (
        "after_state->'metadata'-'last_iteration' IS DISTINCT FROM before_state->'metadata'-'last_iteration'"
        in sql
    )
    assert "paid.charge=b.amounts AND paid.outcome='conservative_unknown'" in sql
    assert "'cost_microunits',0,'mutation_slots',1" in sql
    assert "t.outcome='accepted' AND (child.id IS NULL" in sql
    assert "CREATE TRIGGER" not in sql
