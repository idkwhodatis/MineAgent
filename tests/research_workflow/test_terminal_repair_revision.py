"""Exact additive SQL recipe and immutable predecessor contracts."""

import ast
import re
from pathlib import Path

import pytest

from backend.app import research_workflow_terminal_repair_sql as repair
from backend.app.openevolve_revision_ddl import _function, historical


def literals():
    path = (
        Path(__file__).resolve().parents[2]
        / "backend/alembic/versions/032a_rd_terminal_results.py"
    )
    return {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.Assign)
    }


def test_frozen_maintenance_recipe_exact_and_not_historical_rewrite():
    values = literals()
    assert values["revision"] == "032a_rd_terminal_results"
    assert values["down_revision"] == "032_normal_oe_note_pins"
    assert values["UPGRADE_SQL"] == repair.render_upgrade_sql()
    assert values["DOWNGRADE_SQL"] == repair.render_downgrade_sql()
    assert "JOIN factor_evaluation_outcomes eo" in repair.previous_function()
    assert "JOIN factor_evaluation_outcomes eo" not in values["UPGRADE_SQL"]
    assert_predicate_fixture_copies_complete_frozen_schema_validator_closure()


@pytest.mark.parametrize("revision", ("031", "032"))
def test_unreviewed_predecessor_rejected(monkeypatch, revision):
    original = repair.historical
    monkeypatch.setattr(
        repair,
        "historical",
        lambda r, n: original(r, n) + (" " if r == revision else ""),
    )
    with pytest.raises(ValueError, match="predecessor"):
        repair.render_upgrade_sql()


def test_non_rd_paths_and_all_existing_stop_settlement_guards_preserved():
    old = repair.previous_function()
    new = repair.render_upgrade_sql()
    marker = "  IF (SELECT count(*) FROM research_process_admissions"
    # Only the ambiguous receipt column is qualified in the old trailing guards.
    tail = old[old.index(marker) :].replace(
        "SELECT array_agg(kind ORDER BY kind) FROM rd_provider_transport_receipts WHERE transport_id=t.id",
        "SELECT array_agg(pr.kind ORDER BY pr.kind) FROM rd_provider_transport_receipts pr WHERE pr.transport_id=t.id",
    )
    assert tail in new
    assert (
        old[: old.index("  IF NOT EXISTS(SELECT 1 FROM factor_workflow_submissions")]
        == new[: new.index("  IF NOT EXISTS(SELECT 1 FROM factor_workflow_submissions")]
    )
    assert "Retained RD terminal checkpoint is not representable after032a" in new
    assert repair.render_downgrade_sql().endswith(old)


def assert_predicate_fixture_copies_complete_frozen_schema_validator_closure():
    from tests.integration.workflow_terminal_repair_fixture import SCHEMA, dependencies

    definitions = {
        re.search(r"CREATE OR REPLACE FUNCTION \w+\.(\w+)\(", sql)[1]: sql
        for sql in dependencies()
    }
    pending = {"mineagent_rd_schema"}
    reached = set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        assert name in definitions, f"Missing isolated predicate dependency: {name}"
        reached.add(name)
        body = definitions[name].split("AS $$", 1)[1]
        pending.update(re.findall(r"\b(mineagent_\w+)\s*\(", body))
    assert reached == {
        "mineagent_rd_schema",
        "mineagent_rd_schema_recursive_032e",
        "mineagent_structured_json_value",
    }
    for revision, name in (
        ("032f", "mineagent_rd_schema"),
        ("032f", "mineagent_rd_schema_recursive_032e"),
        ("025", "mineagent_structured_json_value"),
    ):
        assert definitions[name] == _function(
            historical(revision, "UPGRADE_SQL"), name
        ).replace("@SCHEMA@", SCHEMA)
