"""Explicit new public views for a real model-backed structured workflow stage."""

from typing import Literal

from pydantic import model_validator

from packages.research_workflow.contracts import (
    WorkflowStageHistory,
    WorkflowStageView,
    WorkflowView,
)


def _model_stage(value):
    if (
        value.kind != "structured_factor_execution"
        or value.child_run_id is None
        or value.consumption
        not in {
            "not_observed",
            "selected_not_submitted",
            "included_in_submitted_prompt",
            "submission_uncertain",
        }
    ):
        raise ValueError("Actual structured model stage requires explicit consumption")
    return value


class WorkflowOEStageView(WorkflowStageView):
    kind: Literal["structured_factor_execution"] = "structured_factor_execution"

    @model_validator(mode="after")
    def exact_stage_family(self):
        return _model_stage(self)


class WorkflowOEStageHistory(WorkflowStageHistory):
    kind: Literal["structured_factor_execution"] = "structured_factor_execution"

    @model_validator(mode="after")
    def exact_stage_family(self):
        return _model_stage(self)


class StructuredWorkflowView(WorkflowView):
    schema_version: Literal["mineagent.workflow-view.v2"] = "mineagent.workflow-view.v2"
    current_stage: WorkflowStageView | WorkflowOEStageView | None
