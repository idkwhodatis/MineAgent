"""Installed028 initial declarations, not child/provider/process acceptance."""

import pytest
from workflow_declaration_sql_fixture import probe_statements

pytestmark = pytest.mark.postgres


def test_workflow_manual_declaration_and_deferred_forgery_guards_actual_pg(
    migrated_engine,
):
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in probe_statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "WORKFLOW_POPULATED_DECLARATION_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


@pytest.mark.parametrize("layout", [None, "generation_user.v1"])
@pytest.mark.parametrize("drift", [False, True])
def test_actual_child_template_comparison_preserves_layout_only(
    migrated_engine, layout, drift
):
    # Exercise the exact production graph expression, not a duplicated allowlist.
    # Full child receipt/FK/trigger execution remains the workflow E2E gate.
    import json
    import re

    from sqlalchemy import text

    from backend.app.rdagent_workflow_sql import SQL
    from tests.rdagent.test_rdagent_workflow_child_contracts import config

    cfg = config().model_dump(mode="json")
    template = {k: v for k, v in cfg.items() if not k.startswith("workflow_")}
    template.update(
        schema_version="mineagent.factor-research.v2",
        model_profile="configured.rdagent.v1",
    )
    if layout is not None:
        cfg["workflow_prompt_layout"] = layout
    if drift:
        cfg["task"] = {**cfg["task"], "name": "changed-base-task"}
    expression = re.search(
        r"\n OR (\(cfg-ARRAY\[.*?\]\).*?)\n OR \(p.objective",
        SQL,
        re.DOTALL,
    ).group(1)
    with migrated_engine.connect() as connection:
        matches = connection.scalar(
            text(
                "WITH input AS (SELECT CAST(:cfg AS jsonb) AS cfg, "
                "CAST(:plan AS jsonb) AS plan) SELECT NOT ("
                + expression
                + ") FROM input"
            ),
            {
                "cfg": json.dumps(cfg),
                "plan": json.dumps({"research_template": template}),
            },
        )
    assert matches is (not drift)
