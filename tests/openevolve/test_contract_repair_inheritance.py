"""Unpublished successors must not undo the installed027a physical contracts."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "backend/alembic/versions"


def literal(revision):
    paths = list(ROOT.glob(revision + "_*.py"))
    assert len(paths) == 1
    return {
        target.id: ast.literal_eval(node.value)
        for node in ast.parse(paths[0].read_text()).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def test_exact_single_chain_preserves_contract_repair_predecessor(tmp_path):
    assert literal("027a")["down_revision"] == "027_rd_controller_v3"
    assert literal("027b")["down_revision"] == "027a_contract_repairs"
    assert literal("027c")["down_revision"] == "027b_provider_material"
    assert literal("028")["down_revision"] == "027c_seeded_controller_v4"
    assert literal("029")["down_revision"] == "028_oe_structured_search"
    assert literal("030")["down_revision"] == "029_research_workflow"
    assert literal("031")["down_revision"] == "030_declared_upload_basket"
    from script.generate_research_successor_migrations import freeze

    target = tmp_path / "metadata.py"
    target.write_text('down_revision = "027_rd_controller_v3"\n')
    freeze(target, {"down_revision": "027a_contract_repairs"})
    assert target.read_text() == 'down_revision = "027a_contract_repairs"\n'


@pytest.mark.parametrize("revision", ("028", "029", "030", "031"))
@pytest.mark.parametrize("direction", ("UPGRADE_SQL", "DOWNGRADE_SQL"))
def test_successors_do_not_replace_repaired_predicate_or_parent_check(
    revision, direction
):
    sql = literal(revision)[direction]
    functions = re.findall(r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?(\w+)", sql)
    assert "mineagent_structured_contract" not in functions
    assert "mineagent_rd_schema" not in functions
    assert "ck_rd_source_initial_step" not in sql
    assert not re.search(
        r"DROP FUNCTION (?:@SCHEMA@\.)?mineagent_structured_contract\(", sql
    )
