"""Source-only scope contracts; no claim that hosted lock evidence has passed."""

import ast
import json
from pathlib import Path

from script import sql_candidate_proof

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "tests/integration/test_workflow_heartbeat_lock_order.py"


def test_exact_two_revision_inventory_is_separate():
    expected = [
        "tests/integration/test_workflow_heartbeat_lock_order.py::test_real_workflow_heartbeat_crossed_owner_lock_fails_closed[031]",
        "tests/integration/test_workflow_heartbeat_lock_order.py::test_real_workflow_heartbeat_crossed_owner_lock_fails_closed[032g]",
    ]
    assert sql_candidate_proof.inventory("heartbeat_order") == expected
    manifest = json.loads(sql_candidate_proof.MANIFEST.read_text())
    assert all(
        not any("heartbeat_lock_order" in node for node in nodes)
        for group, nodes in manifest.items()
        if group != "heartbeat_order"
    )
    assert '"heartbeat_order"' in (ROOT / "script/sql_candidate_proof.py").read_text()


def test_fixture_uses_actual_admission_claim_and_unchanged_heartbeat():
    source = TEST.read_text()
    for required in (
        '"031_workflow_structured_stage"',
        '"032g_schema_keyset_cost"',
        "legacy._upgrade(source, revision)",
        "with actual_environment(source, settings, monkeypatch) as env:",
        "wid = create_workflow(env)",
        "drive_one(",
        "claim_configured_run(",
        "FactorWorkflowSubmission.run_id",
        '"mineagent.rd-structured-guidance-workflow-plan.v1"',
        '"mineagent.factor-research.v3", "running"',
        ".limit(2)",
        "assert env.settings.lease_seconds == 120",
        "assert env.settings.heartbeat_seconds == 1",
        "from backend.app.services import heartbeat",
        "from workers.research.lease import AttemptLease",
        "assert lease.pulse(force=True) is None",
        "lease.pulse(force=True)",
        "lease.thread.ident is None",
        "SELECT count(*) FROM research_model_operations",
        "SELECT count(*) FROM research_process_admissions",
        "assert env.rd_calls == env.oe_calls == []",
    ):
        assert required in source
    for forbidden in (
        "run_bridge(",
        "drain(",
        "run_worker(",
        ".start(",
        "Thread(",
        "monkeypatch.setattr",
        "render_candidate_sql",
        "DISABLE TRIGGER",
        "CREATE FUNCTION",
        "CREATE TRIGGER",
        "INSERT INTO",
        "UPDATE execution_attempts",
        "lease_seconds=",
        "statement_timeout",
    ):
        assert forbidden not in source

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            assert not any(
                isinstance(target, ast.Attribute)
                and target.attr in {"lease_seconds", "heartbeat_seconds"}
                for target in node.targets
            )


def test_crossed_order_uses_original_deferred_guard_and_exact_rejections():
    source = TEST.read_text()
    assert 'error.orig.sqlstate == "P0001"' in source
    assert 'error.orig.diag.message_primary == "workflow owner is busy"' in source
    assert 'caught.value.orig.sqlstate == "55P03"' in source
    assert source.count("SET CONSTRAINTS ALL IMMEDIATE") == 2
    assert '_try_neutral_lock(owner_session, f"submit:{owner}")' in source
    assert source.index("heartbeat_session.flush()") < source.index(
        "_run_attempt(owner_session, fence)"
    )
    assert source.index("heartbeat_tx.rollback()") < source.rindex(
        "owner_session.flush()"
    )
    assert "if heartbeat_tx.is_active:" in source and "if owner_tx.is_active:" in source
    assert source.count("_require_unchanged(source, before)") == 2
    assert "assert lease.lost.is_set() and lease.cancelled.is_set()" in source
    assert "lease.last_pulse == prior_success" in source
    lock = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_attempt"
    )
    body = ast.unparse(lock)
    assert body.index("research_runs") < body.index("execution_attempts")
    assert body.count("FOR UPDATE NOWAIT") == 2


def test_hosted_step_is_independent_bounded_and_exactly_collected():
    source = (
        ROOT / ".github/workflows/mineagent-workflow-populated-proof.yml"
    ).read_text()
    start = source.index(
        "      - name: Require real finite workflow heartbeat lock order evidence\n"
    )
    stop = source.index(
        "      - name: Require actual current032g finite workflow baseline\n", start
    )
    step = source[start:stop]
    assert (
        "!cancelled() && steps.native_setup.outcome == 'success' && steps.current_baseline_containment.outcome == 'success'"
        in step
    )
    assert "timeout-minutes: 10" in step
    assert 'MINEAGENT_HEARTBEAT_ORDER_REQUIRED: "1"' in step
    assert "--collect-only -q -p no:cacheprovider" in step
    assert "--junitxml=var/numeric-private/heartbeat-order.xml" in step
    assert "--group heartbeat_order" in step
    assert "--output var/numeric-proof/heartbeat-order-outcomes.json" in step
    assert "continue-on-error" not in step
    assert "test_workflow_heartbeat_lock_order_source.py -q" in source
