"""Unregistered workflow receipt metadata, not PostgreSQL graph acceptance."""

from sqlalchemy import UniqueConstraint

from backend.app.rdagent_workflow_models import FactorWorkflowSubmission


def test_workflow_receipt_has_distinct_origin_and_exact_graph_foreign_keys():
    table = FactorWorkflowSubmission.__table__
    foreign = {
        tuple((element.parent.name, element.target_fullname) for element in fk.elements)
        for fk in table.foreign_key_constraints
    }
    assert (
        ("owner_id", "research_workflow_stages.owner_id"),
        ("workflow_id", "research_workflow_stages.workflow_id"),
        ("stage_id", "research_workflow_stages.id"),
    ) in foreign
    assert (
        ("owner_id", "factor_research_specs.owner_id"),
        ("run_id", "factor_research_specs.run_id"),
        ("spec_id", "factor_research_specs.id"),
    ) in foreign
    assert (
        ("owner_id", "factor_analysis_plans.owner_id"),
        ("run_id", "factor_analysis_plans.run_id"),
        ("plan_id", "factor_analysis_plans.id"),
    ) in foreign
    assert table.c.submission_origin.default.arg == "workflow_stage"
    unique = {
        tuple(column.name for column in c.columns)
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert {("owner_id", "stage_id"), ("owner_id", "run_id")} <= unique
    assert all(
        "factor_research_submissions" not in target
        for fk in foreign
        for _, target in fk
    )
