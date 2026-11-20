"""Contracts for separate real-metadata expiry observation tests, not PG evidence."""

import ast
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "integration/test_rd_admission_expiry_observation.py"
)


def test_observation_probe_imports_only_existing_genuine_fixture():
    source = SOURCE.read_text()
    tree = ast.parse(source)
    fixture_import = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "test_workflow_heartbeat_admission_candidate"
    ]
    assert len(fixture_import) == 1
    assert [name.name for name in fixture_import[0].names] == ["candidate_workflow"]
    assert "SET CONSTRAINTS" not in source and "DISABLE TRIGGER" not in source
    assert "CREATE FUNCTION" not in source and "INSERT INTO" not in source
    assert "run_bridge(" not in source and "pulse(" not in source


def test_renewal_probe_preserves_original_heartbeat_and_transaction_boundaries():
    source = SOURCE.read_text()
    for required in (
        "transaction.rollback()",
        "writer.flush()",
        "during = _observe(state)",
        "after_rollback = _observe(state)",
        "fresh = _observe(state)",
        "assert old.cutoff == cutoff",
        "clock=lambda: cutoff + 1",
        "state.fence.lease_token",
        "state.env.settings",
        "_run_attempt(session, state.fence)",
        "_require_unchanged(state.source, before)",
    ):
        assert required in source
    assert source.count("heartbeat(") == 2
    assert "interval '60 seconds'" in source
    assert "lease_seconds =" not in source and "heartbeat_seconds =" not in source


def test_connection_probe_observes_readonly_and_physical_close_without_pool_claim():
    source = SOURCE.read_text()
    for required in (
        '"SHOW transaction_read_only"',
        '"SHOW transaction_isolation"',
        'assert observations == [("on", "read committed")]',
        "observer_connections[0].closed",
        'restored[0] == "off"',
        'event.remove(state.source, "before_cursor_execute", before_cursor_execute)',
    ):
        assert required in source
    assert "NullPool" in source and "not a pooled-reset claim" in source
