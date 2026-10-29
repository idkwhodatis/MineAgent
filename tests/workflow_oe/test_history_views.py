"""New OE guidance metadata is explicit; legacy deterministic views stay strict."""

from uuid import uuid4

import pytest

from packages.research_workflow.contracts import WorkflowStageView
from packages.research_workflow.oe_history import WorkflowOEStageView


def fields():
    return {
        "stage_id": uuid4(),
        "ordinal": 3,
        "kind": "structured_factor_execution",
        "input_revision_id": uuid4(),
        "input_sha256": "a" * 64,
        "definition_sha256": "b" * 64,
        "child_run_id": uuid4(),
        "consumption": "included_in_submitted_prompt",
        "checkpoint_id": None,
    }


def test_legacy_view_cannot_claim_model_consumption():
    with pytest.raises(ValueError):
        WorkflowStageView(**fields())
    assert WorkflowOEStageView(**fields()).consumption == "included_in_submitted_prompt"


@pytest.mark.parametrize(
    "state",
    [
        "not_observed",
        "selected_not_submitted",
        "included_in_submitted_prompt",
        "submission_uncertain",
    ],
)
def test_new_stage_retains_explicit_observation_state(state):
    assert (
        WorkflowOEStageView(**(fields() | {"consumption": state})).consumption == state
    )


@pytest.mark.parametrize(
    "change",
    [
        {"child_run_id": None},
        {"kind": "rd_factor_generation"},
        {"consumption": "not_consumed_numeric"},
    ],
)
def test_new_stage_cannot_invent_child_or_reinterpret_old_family(change):
    with pytest.raises(ValueError):
        WorkflowOEStageView(**(fields() | change))
