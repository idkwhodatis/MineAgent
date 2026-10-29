"""Actual-row fixture construction, not execution of PostgreSQL constraints."""

import pytest

from packages.factors.contracts import digest
from packages.research_workflow.admission import CreateWorkflow, WorkflowCreated
from packages.research_workflow.contracts import WorkflowInputSnapshot
from tests.integration.workflow_declaration_sql_fixture import (
    declaration_rows,
    render_probe_sql,
)


def test_positive_fixture_contains_only_real_declaration_tables_and_commitments():
    rows = {table.name: values for table, values in declaration_rows()}
    assert set(rows) == {
        "users",
        "rd_steers",
        "rd_steer_versions",
        "research_workflows",
        "research_workflow_revisions",
    }
    root = rows["research_workflows"]
    request = CreateWorkflow.model_validate_json(root["creation_canonical"])
    receipt = WorkflowCreated.model_validate_json(root["creation_result_canonical"])
    selection = WorkflowInputSnapshot.model_validate_json(
        rows["research_workflow_revisions"]["inputs_canonical"]
    )
    assert request.plan.sha256 == root["plan_sha256"]
    assert digest(request) == root["creation_sha256"] == receipt.request_sha256
    assert request.refs == selection.refs
    assert (
        selection.refs[0].content_sha256 == rows["rd_steer_versions"]["content_sha256"]
    )
    assert root["current_stage_id"] is root["latest_checkpoint_id"] is None
    assert root["status"] == "blocked"
    assert not rows["users"]["is_active"]


@pytest.mark.parametrize(
    "mutation", ["plan_hash", "selection_hash", "foreign_selection"]
)
def test_negative_declarations_remain_bounded_and_cannot_invent_runtime(mutation):
    rows = {
        table.name: values for table, values in declaration_rows(1, mutation=mutation)
    }
    assert len(rows) == 5
    root = rows["research_workflows"]
    if mutation == "plan_hash":
        assert (
            root["plan_sha256"]
            != CreateWorkflow.model_validate_json(
                root["creation_canonical"]
            ).plan.sha256
        )
    else:
        selection = WorkflowInputSnapshot.model_validate_json(
            rows["research_workflow_revisions"]["inputs_canonical"]
        )
        actual = rows["rd_steer_versions"]
        assert (selection.refs[0].version_id, selection.refs[0].content_sha256) != (
            actual["id"],
            actual["content_sha256"],
        )


def test_sql_requires_real_deferred_checks_and_cannot_swallow_other_errors():
    sql = render_probe_sql()
    assert sql.count("SET CONSTRAINTS ALL IMMEDIATE;") == 5
    assert (
        sql.count("IF SQLERRM <> 'Workflow immutable finite graph differs' THEN RAISE;")
        == 3
    )
    assert "revision=revision+1" in sql
    assert "Invalid immutable workflow/CAS transition" in sql
    assert "Rejected diagnostic mutated root" in sql
    assert "current_stage_id IS NULL AND latest_checkpoint_id IS NULL" in sql
    assert "USING ERRCODE='XX000'" in sql
    assert "DISABLE TRIGGER" not in sql and "session_replication_role" not in sql
    assert "INSERT INTO research_runs" not in sql
    assert "INSERT INTO research_process" not in sql
    assert "WORKFLOW_POPULATED_DECLARATION_MATRIX_COMPLETE" in sql
    with pytest.raises(ValueError, match="Closed negative"):
        declaration_rows(mutation="invented")
