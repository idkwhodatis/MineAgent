"""Additive outbox reverse coverage; no change to historical graph predicates."""

from backend.app import oe_outbox_guard_sql as guard
from backend.app.oe_validation_cost_sql import original


def test_only_evidence_removing_events_schedule_the_existing_checker():
    sql = guard.render_upgrade_sql()
    assert sql.count("CREATE CONSTRAINT TRIGGER ") == 2
    assert sql.count("DEFERRABLE INITIALLY DEFERRED FOR EACH ROW") == 2
    assert sql.count("EXECUTE FUNCTION @SCHEMA@.mineagent_oes_consistent();") == 2
    assert "AFTER DELETE ON @SCHEMA@.outbox_events" in sql
    assert "AFTER UPDATE ON @SCHEMA@.outbox_events" in sql
    assert (
        "WHEN (OLD.run_id IS DISTINCT FROM NEW.run_id\n"
        " OR OLD.generation IS DISTINCT FROM NEW.generation)" in sql
    )
    for forbidden in (
        "INSERT",
        "dispatched_at",
        "job_id",
        "CREATE FUNCTION",
        "CREATE OR REPLACE",
        "DISABLE",
        "DROP",
    ):
        assert forbidden not in sql


def test_reused_checker_keeps_both_targets_and_live_parent_locks():
    sql = original("031_workflow_structured_stage.py", "mineagent_oes_consistent")
    assert "ARRAY[to_jsonb(OLD),to_jsonb(NEW)]" in sql
    assert "ELSIF v ? 'run_id' THEN rid:=(v->>'run_id')::uuid;" in sql
    assert "FOREACH v IN ARRAY versions LOOP" in sql
    assert "current_setting('transaction_isolation')<>'read committed'" in sql
    assert (
        sql.index("FOREACH v IN ARRAY versions LOOP")
        < sql.index("ORDER BY run_id LIMIT 10001")
        < sql.index("FOR UPDATE NOWAIT")
        < sql.index("mineagent_oes_graph_check(target)")
    )
    assert "cardinality(ids)>10000" in sql
    assert "mineagent.oe-structured-provider-run.v2" in sql


def test_downgrade_removes_exactly_the_new_triggers():
    assert guard.render_downgrade_sql().splitlines() == [
        "DROP TRIGGER oes032f_outbox_identity ON @SCHEMA@.outbox_events;",
        "DROP TRIGGER oes032f_outbox_delete ON @SCHEMA@.outbox_events;",
    ]


def test_existing_attachment_audit_precedes_trigger_installation():
    audit = guard.render_preflight_sql()
    assert "FROM @SCHEMA@.oe_workflow_submissions q" in audit
    assert "WHERE NOT EXISTS(SELECT 1 FROM @SCHEMA@.outbox_events" in audit
    assert "WHERE run_id=q.run_id AND generation=1" in audit
    assert "RAISE EXCEPTION" in audit
    assert guard.render_upgrade_sql().startswith(audit + "\nCREATE CONSTRAINT TRIGGER")
    for irrelevant in ("status", "job_id", "dispatched_at", "graph_check", "UPDATE"):
        assert irrelevant not in audit
