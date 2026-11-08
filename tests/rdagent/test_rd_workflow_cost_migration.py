"""Frozen032e uses the same checks and fresh transaction-local commitments."""

import ast
from pathlib import Path

from backend.app.rdagent_workflow_cost_sql import (
    CHILD_CALLS,
    MODEL_TITLES,
    control_scan_sql,
    render_downgrade_sql,
    render_upgrade_sql,
    scalar_child_sql,
    schema_original,
    schema_sql,
    workflow_original,
    workflow_sql,
)
from script.generate_rd_workflow_cost_migration import document


def test_frozen_workflow_cost_migration_is_reproducible():
    path = (
        Path(__file__).parents[2] / "backend/alembic/versions/032e_rd_workflow_cost.py"
    )
    assert path.read_text() == document()
    values = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.Assign)
    }
    assert values["revision"] == "032e_rd_workflow_cost"
    assert values["down_revision"] == "032d_rd_validation_cost"
    assert values["UPGRADE_SQL"] == render_upgrade_sql()
    assert values["DOWNGRADE_SQL"] == render_downgrade_sql()
    assert "from backend" not in path.read_text()
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in path.read_text()


def test_schema_only_inlines_scalar_children_and_control_scan():
    old = "IF EXISTS(SELECT 1 FROM generate_series(1,n) i WHERE ascii(substr(t,i,1))<32 OR ascii(substr(t,i,1))=127) THEN RETURN false; END IF;"
    new = "IF t COLLATE \"C\" ~ ('['||chr(1)||'-'||chr(31)||chr(127)||']') THEN RETURN false; END IF;"
    sql = schema_sql()
    for value, rule in CHILD_CALLS:
        inline = scalar_child_sql(value, rule)
        original = f"     IF NOT @SCHEMA@.mineagent_rd_schema({value},{rule},defs,depth+1) THEN RETURN false; END IF;\n"
        assert sql.count(inline) == 1
        sql = sql.replace(inline, original)
        # The scalar grammar is copied, never manually simplified.
        base = control_scan_sql()
        body = base[
            base.index(" kind:=rule->>'type';") : base.index(" WHEN 'array' THEN")
        ]
        assert body in inline
        assert "IF v IS NULL OR depth>24 THEN RETURN false; END IF;" in inline
        assert "AND NOT (child_rule ?| ARRAY['$ref','anyOf','oneOf'])" in inline
        for title in MODEL_TITLES:
            assert f"'{title}'" in inline
    import re

    special = schema_original().split(" CASE title", 1)[1]
    assert set(re.findall(r" WHEN '([^']+)' THEN", special)) == set(MODEL_TITLES)
    assert sql.replace(new, old) == schema_original()


def test_workflow_only_reuses_same_local_hashes():
    sql = workflow_sql()
    assert sql.count("mineagent_execution_metadata_sha(cfg)") == 1
    assert sql.count("mineagent_execution_metadata_sha(cfg->'input')") == 1
    sql = sql.replace("\n cfg_sha text; input_sha text;", "")
    sql = sql.replace(
        " cfg_sha:=mineagent_execution_metadata_sha(cfg); input_sha:=mineagent_execution_metadata_sha(cfg->'input');\n",
        "",
    )
    sql = sql.replace(
        " IF mineagent_rd_workflow_config(cfg) IS NOT TRUE THEN\n"
        "  RAISE EXCEPTION 'Workflow child immutable graph differs';\n"
        " END IF;\n IF q.submission_origin",
        " IF mineagent_rd_workflow_config(cfg) IS NOT TRUE\n OR q.submission_origin",
    )
    sql = sql.replace("cfg_sha", "mineagent_execution_metadata_sha(cfg)")
    sql = sql.replace("input_sha,", "mineagent_execution_metadata_sha(cfg->'input'),")
    sql = sql.replace("input_sha)", "mineagent_execution_metadata_sha(cfg->'input'))")
    assert sql == workflow_original()
