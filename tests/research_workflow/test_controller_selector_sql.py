"""Optional selector grammar is local; canonical defaults remain omitted."""

import json
import re

import pytest

from backend.app.openevolve_revision_ddl import historical
from backend.app.research_workflow_sql import render_upgrade_sql
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.admission import CreateWorkflow
from packages.research_workflow.plan import RDGuidancePlan
from tests.integration.workflow_controller_selector_fixture import (
    V3,
    statements,
    variant_rows,
)


@pytest.mark.parametrize("kind", ["plan", "create"])
def test_only_optional_selector_is_projected_from_exact_object_schema(kind):
    sql = render_upgrade_sql()
    rule = json.loads(
        re.search(f"WHEN '{kind}' THEN rule := '(.*?)'::jsonb;", sql)[1].replace(
            "''", "'"
        )
    )
    node = rule["$defs"]["WorkflowProviderResearchTemplate"]
    assert "controller_profile" not in node["properties"]
    assert set(node["required"]) == set(node["properties"])
    assert "cfg->'controller_profile' IS DISTINCT FROM" in sql
    assert "v:=jsonb_set(v,path,cfg-'controller_profile')" in sql


@pytest.mark.parametrize("selector", ["missing", V3])
def test_actual_canonical_models_keep_missing_or_exact_new_choice(selector):
    _, plan, request = variant_rows(90, selector)
    assert canonical_bytes(
        RDGuidancePlan.model_validate_json(canonical_bytes(plan))
    ) == canonical_bytes(plan)
    assert canonical_bytes(
        CreateWorkflow.model_validate_json(canonical_bytes(request))
    ) == canonical_bytes(request)
    assert ("controller_profile" in plan["research_template"]) == (selector == V3)


@pytest.mark.parametrize(
    "selector",
    [
        None,
        False,
        60,
        {},
        "mineagent.rdagent-orchestrator.v2",
        "mineagent.rdagent-orchestrator.v4",
        "unknown",
    ],
)
def test_explicit_invalid_selector_is_not_omission(selector):
    _, plan, request = variant_rows(90, selector)
    for model, value in ((RDGuidancePlan, plan), (CreateWorkflow, request)):
        with pytest.raises(ValueError):
            model.model_validate_json(canonical_bytes(value))


def test_retained_pg_corpus_exercises_deferred_rows_and_exact_bytes():
    sql = "\n".join(statements())
    assert sql.count("Controller selector parity") == 16
    assert sql.count("Controller budget parity") == 4
    assert sql.count("SET CONSTRAINTS ALL IMMEDIATE") == 2
    assert "plan_canonical=" in sql


def test_030_preserves_exact_029_optional_guard_while_adjusting_claim_bound():
    from backend.app.rdagent_provider_revision import function

    before = function(historical("029", "UPGRADE_SQL"), "mineagent_workflow_payload")
    after = function(historical("030", "UPGRADE_SQL"), "mineagent_workflow_payload")
    guard = before[before.index(" IF kind IN") : before.index(" CASE kind")]
    assert guard in after
