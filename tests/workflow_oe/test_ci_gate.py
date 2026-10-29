"""Required hosted wiring only, not local execution of the workflow engine."""

import ast
from pathlib import Path

WORKFLOW = Path(".github/workflows/mineagent-structured-factor.yml")


def step(name):
    text = WORKFLOW.read_text()
    start = text.index("      - name: " + name + "\n")
    end = text.find("      - name:", start + 1)
    return text[start : end if end >= 0 else None]


def assert_required_case_names(source, gate):
    sources = (source,) if isinstance(source, str) else source
    names = {
        node.name
        for item in sources
        for node in ast.parse(Path(item).read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    assert all(item in gate for item in sources)
    assert f"assert len(cases)=={len(names)}" in gate
    for name in names:
        assert repr(name) in gate
    assert "('failure','error','skipped')" in gate
    assert 'test "$status" = 0' in gate
    assert "--junitxml=var/numeric-private/" in gate
    assert "summarize(source,Path('var/numeric-proof/" in gate
    return names


def test_installed_workflow_and_note_gate_requires_all_six_real_pg_cases():
    gate = step("Require installed finite workflow OE contracts and reverse lineage")
    names = assert_required_case_names(
        (
            "tests/integration/test_workflow_oe_persistence.py",
            "tests/integration/test_normal_oe_note_persistence.py",
        ),
        gate,
    )
    assert len(names) == 6
    assert "timeout-minutes: 15" in gate


def test_actual_finite_stage_and_restore_gate_cannot_skip_or_use_missing_images():
    gate = step("Require actual finite workflow RD to OE stage and complete restore")
    names = assert_required_case_names(
        "tests/integration/test_workflow_structured_stage_flow.py", gate
    )
    assert len(names) == 1
    assert 'MINEAGENT_WORKFLOW_STRUCTURED_FLOW_REQUIRED: "1"' in gate
    assert 'MINEAGENT_RD_WORKFLOW_BROWSER_REQUIRED: "1"' in gate
    assert 'MINEAGENT_OE_STRUCTURED_NORMAL_REQUIRED: "1"' in gate
    assert "timeout-minutes: 40" in gate
    for variable in (
        "CANDIDATE_IMAGE",
        "NORMALIZER_IMAGE",
        "ORCHESTRATOR_IMAGE",
        "ANALYSIS_IMAGE",
        "QLIB_INSTALLED_IMAGE",
        "OE_STRUCTURED_RUNTIME_PROFILE",
    ):
        assert f'export MINEAGENT_{variable}="$(cat var/numeric-proof/' in gate
    assert 'RUN_BACKUP_RESTORE_DRILL: "1"' in WORKFLOW.read_text()


def test_local_workflow_contract_gate_includes_new_stage_and_consumption_suite():
    workflow = Path(".github/workflows/mineagent-configured-analysis.yml").read_text()
    assert (
        "python -m pytest tests/research_workflow tests/research_inputs tests/workflow_oe -q"
        in workflow
    )


def test_successful_workflow_root_is_created_by_browser_before_same_root_runs():
    source = Path("tests/integration/workflow_structured_stage_fixture.py").read_text()
    module = ast.parse(source)
    functions = {n.name: n for n in module.body if isinstance(n, ast.FunctionDef)}
    bridge = functions["run_bridge"]
    creates = [
        n
        for n in ast.walk(bridge)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "create_workflow"
    ]
    assert len(creates) == 1
    assert {k.arg: ast.unparse(k.value) for k in creates[0].keywords} == {
        "browser_engine": "engine",
        "browser_tmp_path": "tmp_path",
    }
    create = ast.unparse(functions["create_workflow"])
    assert "assert_actual_workflow_creation" in create
    assert "json=observed['envelope']" in create
    assert "replay.json() == observed['receipt']" in create
    assert "no_engine_child(env.factory, wid)" in create
    flow = Path("tests/integration/test_workflow_structured_stage_flow.py").read_text()
    assert "wid = create_workflow(env)" in flow  # HTTP control-only roots remain.
    assert "completed = run_bridge(env, source, tmp_path)" in flow
    assert flow.index("completed = run_bridge") < flow.index(
        "assert_restore(drill_databases"
    )


def test_creation_browser_helper_strictly_dispatches_both_public_versions():
    source = Path("tests/integration/workflow_history_browser.py").read_text()
    assert 'schema == "mineagent.workflow-public-request.v1"' in source
    assert 'schema == "mineagent.workflow-public-request.v2"' in source
    assert "request_type, submit_type = WorkflowRequest, WorkflowSubmit" in source
    assert (
        "request_type, submit_type = StructuredWorkflowRequest, StructuredWorkflowSubmit"
        in source
    )
    assert (
        'raise AssertionError("Exact public workflow request version required")'
        in source
    )
    assert "request_type.model_validate_json" in source
    assert "submit_type.model_validate_json" in source
    assert "model_construct" not in source
    assert "ProcessOwner(time.monotonic() + 180)" in source
    assert "seconds=120" in source
