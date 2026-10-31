"""Exact maintained fixture revision and unchanged cold-restore boundaries."""

import ast
from pathlib import Path


def test_normal_flow_and_restore_pin_numeric_maintenance_revision():
    root = Path(__file__).parents[1] / "integration"
    flow = ast.parse((root / "test_oe_structured_normal_flow.py").read_text())
    entry = next(
        node
        for node in flow.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_normal_demo_generated_factor_and_model_child_reach_actual_engine"
    )
    calls = [node for node in ast.walk(entry) if isinstance(node, ast.Call)]
    upgrade = next(
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "_upgrade"
    )
    assert ast.literal_eval(upgrade.args[1]) == "032a_rd_terminal_results"
    restore = next(
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "assert_normal_restore"
    )
    assert (
        next(
            ast.literal_eval(k.value)
            for k in restore.keywords
            if k.arg == "expected_revision"
        )
        == "032a_rd_terminal_results"
    )
    source = (root / "oe_structured_restore_replay.py").read_text()
    helper = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "assert_normal_restore"
    )
    assert ast.literal_eval(helper.args.kw_defaults[-1]) == "028_oe_structured_search"
    closure_if = next(
        n
        for n in ast.walk(helper)
        if isinstance(n, ast.If)
        and any(
            isinstance(x, ast.Constant) and x.value == "declared_upload_closure"
            for statement in n.body
            for x in ast.walk(statement)
        )
    )
    assert isinstance(closure_if.test, ast.Compare)
    assert ast.literal_eval(closure_if.test.comparators[0]) == {
        "030_declared_upload_basket",
        "032a_rd_terminal_results",
    }
    assert 'assert manifest["schema_revision"] == expected_revision' in source
    assert "assert after[key] == before[key]" in source
    matrix = (root / "declared_upload_numeric_fixture.py").read_text()
    assert "statements.extend(normal_config_revision_statements())" in matrix
    assert 'historical("028", "UPGRADE_SQL")' in matrix
    assert "((0.5, False), (1.0, True))" in matrix
