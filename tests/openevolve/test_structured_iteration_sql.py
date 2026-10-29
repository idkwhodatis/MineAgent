"""Partial producer relationships and literal construction, not actual SQL execution."""

from backend.app import oe_structured_iteration_sql as iteration


def test_real_typed_placement_kernel_context_and_rendered_contracts():
    sql = iteration.render_upgrade_sql()
    assert "@RULES@" not in sql
    for kind in ("placement", "kernel", "context", "rendered"):
        assert f"WHEN '{kind}'" in sql
    assert "worker->>'host_pid')::bigint NOT BETWEEN 1 AND 2147483647" in sql
    assert "worker->>'start_ticks')::bigint<1" in sql
    assert (
        "i.rendered_canonical::jsonb->>'current_candidate_sha256' IS DISTINCT FROM parent.candidate_sha256"
        in sql
    )


def test_partial_sampling_does_not_require_future_worker_or_sdk():
    sql = iteration.SQL.split("CREATE FUNCTION @SCHEMA@.mineagent_oes_placement_check")[
        0
    ]
    assert "oe_structured_worker_placements" not in sql
    assert "oe_structured_iteration_intents" not in sql
    assert "mineagent_oes_population_check(previous.id)" in sql
    assert "'engine_mutation','oe-structured-mutation:'" in sql
    assert "'cost_microunits',0,'mutation_slots',1" in sql


def test_placement_and_final_intent_bind_original_ticket_and_observed_identity():
    sql = iteration.render_upgrade_sql()
    for text in (
        "p.controller_identity_sha256 IS DISTINCT FROM",
        "worker->>'ppid'",
        "child.payload->'controller'->>'host_pid'",
        "i.worker_ticket_sha256",
        "si.worker_ticket_sha256",
        "p.worker_identity_canonical,p.worker_identity_sha256",
    ):
        assert text in sql
    assert "CREATE TRIGGER" not in sql
