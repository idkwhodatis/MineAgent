"""SQL construction checks; actual PostgreSQL semantics remain a hosted gate."""

import pytest

from backend.app.openevolve_worker_ddl import SQL, TABLES, render_sql


@pytest.mark.parametrize("schema", [None, True, "", "A", "a.b", "a;drop", "a" * 64])
def test_schema_is_closed(schema):
    with pytest.raises(ValueError):
        render_sql(schema)


def test_all_cross_row_dependencies_are_deferred():
    sql = render_sql("worker_proof")
    assert "@S@" not in sql
    assert len(TABLES) == len(set(TABLES)) == 16
    for table in TABLES:
        assert f"ON worker_proof.{table} DEFERRABLE INITIALLY DEFERRED" in sql
    assert "SET search_path=worker_proof,pg_catalog" in sql


def test_sampling_ticket_binds_actual_control_and_retained_event_hash():
    for fragment in (
        "event_row.intent_id,event_row.native_iteration",
        "event_row.event_kind<>'iteration_intent'",
        "control_row.sampling_intent_id",
        "event_row.command_sha256,i.id",
        "control_row.kind<>'sampling'",
        "'search_controller_event'::varchar,event_row.event_sha256",
    ):
        assert fragment in SQL


def test_sampling_before_placement_remains_a_pending_obligation():
    pending = SQL.split("SELECT * INTO x FROM search_worker_expected_identities")[
        1
    ].split("IF (x.owner_id")[0]
    assert "IF NOT FOUND THEN" in pending
    assert "NOT EXISTS(SELECT 1 FROM search_worker_assignments" in pending
    assert "NOT EXISTS(SELECT 1 FROM search_worker_outcomes" in pending
    assert "RETURN true" not in pending
    assert "INSERT INTO" not in SQL


def test_expected_worker_binds_controller_child_not_leader_or_caller_pid():
    assert "kind='controller_child'" in SQL
    assert "(child#>>'{controller,host_pid}')::integer=x.host_pid" in SQL
    assert (
        "child#>>'{controller,pid_namespace}' IS DISTINCT FROM x.pid_namespace" in SQL
    )
    assert "child#>>'{controller,cgroup}' IS DISTINCT FROM x.cgroup" in SQL
    assert "'search_worker_identity'::varchar,x.evidence_sha256" in SQL


def test_assignment_requires_exact_expected_identity_and_four_kernel_fields():
    assert "assigned.ticket_id,assigned.expected_identity_id" in SQL
    assert (
        "assigned.host_pid,assigned.start_ticks,assigned.pid_namespace,assigned.cgroup"
        in SQL
    )
    assert "t.id,x.id,x.host_pid,x.start_ticks,x.pid_namespace,x.cgroup" in SQL


def test_single_worker_assignments_form_closed_prior_prefix():
    assert "<>i.native_iteration-1" in SQL
    assert "wi.native_iteration<i.native_iteration" in SQL
    assert "wc.assignment_id=wa.id" in SQL


def test_outcome_and_assignment_close_atomically_in_both_directions():
    assert "WHERE id=closed.worker_outcome_id" in SQL
    assert "outcome_row.intent_id,outcome_row.native_iteration" in SQL
    assert "c.worker_outcome_id=o.id AND c.intent_id=o.intent_id" in SQL
    assert (
        "NOT EXISTS(SELECT 1 FROM search_worker_outcomes WHERE intent_id=i.id)" in SQL
    )


def test_native_outcome_candidate_ack_and_parent_source_are_exact():
    assert "grant_row.intent_id,grant_row.admission_id" in SQL
    assert "candidate.parent_candidate_id,candidate.parent_source_sha256" in SQL
    assert "i.parent_candidate_id,i.parent_source_sha256" in SQL
    assert (
        "(objective.status='eligible') IS DISTINCT FROM (outcome_row.status='evaluated')"
        in SQL
    )
    assert (
        "outcome_row.status='worker_error' AND outcome_row.candidate_id IS NULL" in SQL
    )


def test_sql_does_not_invent_physical_evidence_or_live_authority():
    assert "UPDATE " not in SQL
    assert "DELETE FROM" not in SQL
    assert "INSERT INTO" not in SQL
    assert "pg_read_file" not in SQL
    assert "EXCEPTION WHEN OTHERS THEN RETURN false" in SQL
    assert "IS DISTINCT FROM CASE" not in SQL
