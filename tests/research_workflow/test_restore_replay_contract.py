"""Portable wiring checks only; the same required actual cases execute restore."""

import ast
from pathlib import Path

HELPER = Path("tests/integration/workflow_restore_replay.py")
FLOW = Path("tests/integration/test_rdagent_workflow_user_flow.py")


def test_roundtrip_is_same_fixture_and_never_adds_execution_or_disclosure():
    source = HELPER.read_text()
    tree = ast.parse(source)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert functions == {"_paths", "assert_workflow_restore"}
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"_roundtrip", "_disabled_settings", "_inventory", "create_app"} <= calls
    assert (
        not {
            "drive_one",
            "submit_workflow",
            "dispatch_outbox",
            "claim_attempt",
            "execute_configured_controller",
        }
        & calls
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"post", "put", "delete", "patch"}
        for node in ast.walk(tree)
    )
    assert 'manifest["schema_revision"] == "029_research_workflow"' in source
    assert '"workflow_closure"' in source and '"research_input_closure"' in source
    assert "final[key] == before[key]" in source
    assert "_files(target) == files" in source
    assert source.count("_inventory(restored_factory) == inventory") == 2
    assert "response.content" in source and "buffers[path]" in source
    assert "api.get(path, headers=foreign).status_code == 404" in source


def test_actual_queries_bound_exact_workflow_sources_and_outputs():
    source = HELPER.read_text()
    assert "owner_id=owner_id, workflow_id=workflow_id" in source
    assert ".limit(18)" in source and ".limit(65)" in source
    assert "ResearchInputVersion.derivation_id.in_(ids)" in source
    assert "expected_sha256={version.content_sha256}" in source
    assert "expected_sha256={obj.sha256}" in source
    assert "feedback-versions" in source
    assert "/selected-inputs" in source and "/stages?limit=32" in source


def test_both_existing_cases_restore_after_real_browser_without_extra_case():
    source = FLOW.read_text()
    tree = ast.parse(source)
    tests = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    assert len(tests) == 1
    assert source.count("assert_restored_history(wid)") == 2
    assert source.count("assert_browser_history(wid)") == 2
    assert (
        "assert_browser_history(wid)\n                    assert_restored_history(wid)\n                    return"
        in source
    )
    assert (
        "assert_browser_history(wid)\n                assert_restored_history(wid)"
        in source
    )
    assert '"MINEAGENT_RD_WORKFLOW_FLOW_REQUIRED"' in source
