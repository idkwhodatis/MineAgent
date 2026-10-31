"""Frozen028 candidate metadata, not installed graph/worker acceptance."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app import research_workflow_revision_ddl as ddl
from backend.app.core.db import REQUIRED_SCHEMA_REVISION
from backend.app.schema_capabilities import capabilities_for


def literal():
    tree = ast.parse(
        Path("backend/alembic/versions/029_research_workflow.py").read_text()
    )
    return {
        t.id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }


def test_literal_exact_verified_renderer_and_single_successor():
    value = literal()
    assert value["revision"] == "029_research_workflow"
    assert REQUIRED_SCHEMA_REVISION == "032b_oe_append_run_state"
    successor = ast.parse(
        Path("backend/alembic/versions/030_declared_upload_basket.py").read_text()
    )
    metadata = {
        target.id: ast.literal_eval(node.value)
        for node in successor.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert metadata["revision"] == "030_declared_upload_basket"
    final_tree = ast.parse(
        Path("backend/alembic/versions/031_workflow_structured_stage.py").read_text()
    )
    final = {
        target.id: ast.literal_eval(node.value)
        for node in final_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert final["revision"] == "031_workflow_structured_stage"
    note_tree = ast.parse(
        Path("backend/alembic/versions/032_normal_oe_note_pins.py").read_text()
    )
    note = {
        target.id: ast.literal_eval(node.value)
        for node in note_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert note["revision"] == "032_normal_oe_note_pins"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config("backend/alembic.ini"))
    maintenance = scripts.get_revision(REQUIRED_SCHEMA_REVISION)
    assert maintenance.down_revision == "032a_rd_terminal_results"
    terminal = scripts.get_revision(maintenance.down_revision)
    assert terminal.down_revision == note["revision"]
    assert scripts.get_heads() == [REQUIRED_SCHEMA_REVISION]
    assert note["down_revision"] == final["revision"]
    assert final["down_revision"] == metadata["revision"]
    assert metadata["down_revision"] == value["revision"]
    assert value["down_revision"] == "028_oe_structured_search"
    assert value["TABLE_SQL"] + "\n" + value["UPGRADE_SQL"] == ddl.render_upgrade_sql()
    assert value["DOWNGRADE_SQL"] == ddl.render_downgrade_sql()
    assert len(value["NEW_TABLES"]) == 12
    assert not set(value["NEW_TABLES"]) & set(value["EXISTING_TABLES"])


def test_exact_tuple_preserves_normal_and_all_prior_capabilities():
    old = capabilities_for("028_oe_structured_search")
    new = capabilities_for("029_research_workflow")
    for key, value in vars(old).items():
        if key not in {
            "research_closure_schema",
            "rd_source_closure_schema",
            "workflow_schema",
            "research_input_schema",
        }:
            assert getattr(new, key) == value
    assert new.research_closure_schema == "mineagent.research-restore-closure.v14"
    assert new.rd_source_closure_schema == "mineagent.rd-source-restore-closure.v9"
    assert new.workflow_schema == "mineagent.research-workflow-closure.v1"
    assert new.research_input_schema == "mineagent.research-inputs.v1"
    assert old.workflow_schema is old.research_input_schema is None


@pytest.mark.parametrize(
    "module",
    [
        "backend.app.models",
        "backend.app.research_workflow_models",
        "backend.app.research_input_models",
        "backend.app.rdagent_workflow_models",
    ],
)
def test_fresh_import_orders_register_exact_twelve_new_tables(module):
    script = f"import {module}; from backend.app.models import Base; from backend.app.research_workflow_persistence_ddl import TABLES; assert set(TABLES)<=set(Base.metadata.tables)"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_workflow_provider_upgrade_and_downgrade_preserve_027c_material():
    from backend.app import rdagent_provider_material_sql as material
    from backend.app import rdagent_workflow_provider_revision as provider

    original = provider.original("mineagent_rd_provider_graph")
    from backend.app.openevolve_revision_ddl import historical
    from backend.app.rdagent_provider_revision import function

    assert original == function(
        historical("027c", "UPGRADE_SQL"), "mineagent_rd_provider_graph"
    )
    assert "PERFORM mineagent_rd_v4_seed_binding(rid)" in original
    upgraded = provider.replacements()["mineagent_rd_provider_graph"]
    assert material.MATERIAL_SQL in upgraded
    assert material.BINDING_SQL in upgraded
    assert "mineagent_rd_workflow_submission_check" in upgraded
    assert original in provider.render_downgrade_sql()
    assert original in literal()["DOWNGRADE_SQL"]
    assert upgraded in literal()["UPGRADE_SQL"]


def test_workflow_material_predecessor_pin_is_closed(monkeypatch):
    from backend.app import rdagent_workflow_provider_revision as provider

    monkeypatch.setitem(provider.PINS, "027c_seeded_controller_v4.py", "0" * 64)
    with pytest.raises(ValueError, match="predecessor bytes"):
        provider.render_upgrade_sql()
    with pytest.raises(ValueError, match="predecessor bytes"):
        provider.render_downgrade_sql()
