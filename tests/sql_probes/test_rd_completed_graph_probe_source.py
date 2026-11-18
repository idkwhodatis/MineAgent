"""Source contracts only; actual terminal-v2 proof requires the native CI hook."""

import ast
import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tests/integration/rd_completed_graph_probe.py"
NATIVE = ROOT / "tests/integration/test_rdagent_provider_user_flow.py"
WORKFLOW = ROOT / ".github/workflows/mineagent-configured-analysis.yml"


def helper_module():
    spec = importlib.util.spec_from_file_location("completed_rd_source_test", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_follows_all_original_native_assertions_without_replacing_any():
    tree = ast.parse(NATIVE.read_text())
    test = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_normal_provider_")
    )
    parents = {
        child: parent
        for parent in ast.walk(test)
        for child in ast.iter_child_nodes(parent)
    }
    hooks = [
        n
        for n in ast.walk(test)
        if isinstance(n, ast.If)
        and "MINEAGENT_COMPLETED_RD_GRAPH_PROBE_REQUIRED" in ast.unparse(n.test)
    ]
    assert len(hooks) == 1
    hook = hooks[0]
    parent = parents[hook]
    index = parent.body.index(hook)
    assert (
        ast.unparse(parent.body[index - 1])
        == "assert derived_guidance in seeded_prompt"
    )
    assert index == len(parent.body) - 1
    assert (
        ast.unparse(hook.test)
        == "os.environ.get('MINEAGENT_COMPLETED_RD_GRAPH_PROBE_REQUIRED') == '1'"
    )
    assert ast.unparse(hook.body[-1]) == "assert len(captured) == provider_call_count"
    assert "prove_completed_rd_graph(engine, rid, child_run_id)" in ast.unparse(hook)
    parent.body.pop(index)
    assert (
        hashlib.sha256(ast.dump(test, include_attributes=False).encode()).hexdigest()
        == "d98b2c2c2b98ed4d61db0c62efd4d811a42b34d31d621597fdd2e67ba2dd9d53"
    )


def test_existing_24_native_cases_and_step_are_unchanged_except_required_probe():
    source = WORKFLOW.read_text()
    step = next(
        s
        for s in re.split(r"(?=^      - )", source, flags=re.MULTILINE)
        if s.startswith(
            "      - name: Require actual configured results browser and cold restore\n"
        )
    )
    env = '        env:\n          MINEAGENT_COMPLETED_RD_GRAPH_PROBE_REQUIRED: "1"\n'
    proof = """          import json
          proof=json.loads(Path('var/numeric-proof/rd-completed-v2-proof.json').read_text())
          assert proof=={'schema':'mineagent.completed-rd-v2-baseline.v1','revision':'032g_schema_keyset_cost','completed_runs':2,'timestamp_rollback_cases':2,'identity_rejection_cases':2,'nowait_contention_cases':4,'candidate_activated':False}
"""
    assert step.count(env) == step.count(proof) == 1
    original = step.replace(env, "", 1).replace(proof, "", 1)
    assert (
        hashlib.sha256(original.encode()).hexdigest()
        == "a49bc9c5b3b9317af44283f7a52817ed4dd00c30ce8c5dca046407fee4295287"
    )
    assert "assert len(cases)==24" in step
    assert "('failure','error','skipped')" in step
    assert source.count("MINEAGENT_COMPLETED_RD_GRAPH_PROBE_REQUIRED") == 1


def test_probe_preserves_original_guards_and_only_uses_owned_terminal_graph():
    source = HELPER.read_text()
    for required in (
        '"028_oe_structured_search"',
        'legacy._upgrade(engine, "032g_schema_keyset_cost")',
        '"mineagent.factor-research.v2"',
        "public.mineagent_rd_provider_graph(:id)",
        "public.mineagent_rd_source_violation(id)",
        "SET TRANSACTION READ ONLY",
        "SET CONSTRAINTS ALL IMMEDIATE",
        "SET LOCAL lock_timeout='2s'",
        "generation=generation+1",
        'caught.value.orig.sqlstate == "23503"',
        'caught.value.orig.sqlstate == "55P03"',
        "detail.constraint_name",
        "scope_constraints",
        "=ARRAY['run_id','generation','attempt_id']",
        "=ARRAY['run_id','generation','id']",
        "transaction.rollback()",
        "LIMIT 16385",
        "_require_unchanged(engine, before)",
    ):
        assert required in source
    for forbidden in (
        "render_candidate_sql",
        "oe_trigger_target_probe",
        "DISABLE TRIGGER",
        "CREATE FUNCTION",
        "CREATE TRIGGER",
        "DROP ",
        "stamp(",
        ".commit(",
        "create_queue_app",
        "run_worker",
        ".pulse(",
        "statement_timeout",
        "INSERT INTO",
        "DELETE FROM",
        "sleep(",
        "Thread(",
        "session.add(",
    ):
        assert forbidden not in source
    assert source.count("UPDATE execution_attempts SET") == 3
    lock = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_lock_run_attempt"
    )
    assert ast.unparse(lock).index("research_runs") < ast.unparse(lock).index(
        "execution_attempts"
    )
    assert ast.unparse(lock).count("FOR UPDATE NOWAIT") == 2


def test_only_fixed_sanitized_proof_is_exported():
    module = helper_module()
    assert module.PROOF_PATH == Path("var/numeric-proof/rd-completed-v2-proof.json")
    assert module.PROOF == {
        "schema": "mineagent.completed-rd-v2-baseline.v1",
        "revision": "032g_schema_keyset_cost",
        "completed_runs": 2,
        "timestamp_rollback_cases": 2,
        "identity_rejection_cases": 2,
        "nowait_contention_cases": 4,
        "candidate_activated": False,
    }
    source = HELPER.read_text()
    assert "json.dumps(PROOF, sort_keys=True)" in source
    assert source.count(".write_text(") == 1 and "print(" not in source
    assert "assert not PROOF_PATH.exists()" in source


def test_changed_snapshot_fails_without_rendering_private_rows(monkeypatch):
    module = helper_module()
    monkeypatch.setattr(module, "_snapshot", lambda *_: "PRIVATE_ROW_DATA")
    with pytest.raises(AssertionError) as caught:
        module._require_unchanged(object(), "PRIVATE_OTHER_DATA")
    assert str(caught.value) == "Completed RD probe changed retained schema or rows"


def test_source_guard_and_paths_include_new_proof_without_old_inventory_changes():
    independent = (
        ROOT / ".github/workflows/mineagent-workflow-populated-proof.yml"
    ).read_text()
    assert (
        "tests/rdagent/test_rpc_stage_diagnostic.py tests/sql_probes/test_rd_completed_graph_probe_source.py tests/sql_probes/test_workflow_heartbeat_lock_order_source.py tests/sql_probes/test_heartbeat_schema_inventory_source.py tests/sql_probes/test_rd_attempt_admission_candidate.py tests/sql_probes/test_workflow_heartbeat_admission_source.py tests/sql_probes/test_rd_admission_expiry_observation_unit.py tests/sql_probes/test_rd_admission_expiry_observation_source.py -q"
        in independent
    )
    for source in (independent, WORKFLOW.read_text()):
        assert '"tests/integration/rd_completed_graph_probe.py"' in source
        assert '"tests/sql_probes/test_rd_completed_graph_probe_source.py"' in source
