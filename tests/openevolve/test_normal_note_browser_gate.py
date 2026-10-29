"""Static required wiring only; these tests do not claim browser or engine success."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_source_browser_edits_and_restored_browser_keeps_read_only_default():
    source = (ROOT / "tests/integration/normal_oe_note_flow.py").read_text()
    tree = ast.parse(source)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    for name, writes in (
        ("create_released_notes", True),
        ("assert_notes_restore", False),
    ):
        calls = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "assert_normal_oe_notes_in_browser"
        ]
        assert len(calls) == 1
        keywords = {item.arg: item.value for item in calls[0].keywords}
        assert ("edit" in keywords) is writes
        if writes:
            assert ast.literal_eval(keywords["edit"]) is True
    # The normal RD/OE graph and its exact031 restore precede all notes.
    flow = (
        ROOT / "tests/integration/test_workflow_structured_stage_flow.py"
    ).read_text()
    assert flow.index("completed = run_bridge(env, source, tmp_path)") < flow.index(
        "assert_restore(drill_databases"
    )
    assert flow.index("assert_restore(drill_databases") < flow.index(
        "notes = create_released_notes"
    )
    assert "assert (len(env.rd_calls), len(env.oe_calls)) == call_counts" in flow


def test_browser_composer_uses_actual_clicks_and_keeps_fixed_process_limits():
    text = (ROOT / "frontend/tests/oe-normal-acceptance/normal.spec.ts").read_text()
    block = text.split('if (input.mode === "normal_notes_edit") {', 1)[1].split(
        "  save({", 1
    )[0]
    assert "request.post" not in block and "route.fulfill" not in block
    assert "result.execution.fills[0]" in block
    assert 'name: "Capture exact released note evidence"' in block
    assert block.count('name: "Save exact normal note revision"') == 2
    assert "expect(submitted.base_revision).toBe(1)" in block
    assert "expect(submitted.base_revision_sha256).toBe(first.revision_sha256)" in block
    assert "await page.reload()" in block
    assert "toEqual(fill)" in block
    assert "expect(calls).toEqual([" in block
    helper = (ROOT / "tests/integration/oe_structured_browser.py").read_text()
    assert "seconds=120" in helper and "ProcessOwner(time.monotonic() + 180)" in helper
    assert "len(cases) == 1" in helper
    assert 'for tag in ("failure", "error", "skipped")' in helper


def test_browser_receipts_are_reloaded_and_retained_not_trusted_as_database_rows():
    helper = (ROOT / "tests/integration/oe_structured_browser.py").read_text()
    assert "assert after[0] == before[0]" in helper
    assert "assert observed == [] and after == before" in helper
    assert "session.get(ResearchNote, note_id)" in helper
    assert "after[2] - before[2] == {row.id for row in rows}" in helper
    assert "after[3] - before[3] == {pin.id for pin in pins}" in helper
    flow = (ROOT / "tests/integration/normal_oe_note_flow.py").read_text()
    assert "for item in browser_revisions:" in flow
    assert 'revision["content"]["evidence"] == [references[2]]' in flow
    assert "response = env.client.get(path + query, headers=env.headers)" in flow
    assert "assert all(after[key] == reread[key] for key in closures)" in flow
