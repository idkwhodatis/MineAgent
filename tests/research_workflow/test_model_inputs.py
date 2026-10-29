from hashlib import sha256
from uuid import uuid4

import pytest

from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.resolved import ResolvedInputVersion
from packages.research_workflow.contracts import (
    StageExecutionBinding,
    WorkflowInputSnapshot,
)
from packages.research_workflow.model_inputs import ModelStageInputSnapshot


def snapshot():
    owner, workflow, revision, stage = (uuid4() for _ in range(4))
    refs = tuple(
        InputVersionRef(
            origin=origin,
            record_id=uuid4(),
            version_id=uuid4(),
            content_sha256="a" * 64,
        )
        for origin in (
            "manual_user_submission",
            "normalized_external_proposal",
            "experiment_feedback_proposal",
        )
    )
    selection = WorkflowInputSnapshot(
        owner_id=owner, workflow_id=workflow, revision_id=revision, refs=refs
    )
    binding = StageExecutionBinding(
        owner_id=owner,
        workflow_id=workflow,
        stage_id=stage,
        input_revision_id=revision,
        input_sha256=selection.sha256,
        definition_sha256="b" * 64,
    )
    resolved = tuple(
        ResolvedInputVersion(
            ref=ref,
            kind="hypothesis",
            title="Hypothesis",
            text="Research a bounded hypothesis",
            text_sha256=sha256(b"Research a bounded hypothesis").hexdigest(),
        )
        for ref in refs
    )
    return ModelStageInputSnapshot(
        binding=binding, selection=selection, inputs=resolved
    )


def test_mixed_origins_never_become_user_submission_origin():
    value = snapshot()
    assert value.binding.submission_origin == "workflow_stage"
    assert tuple(row.ref.origin for row in value.inputs) == (
        "manual_user_submission",
        "normalized_external_proposal",
        "experiment_feedback_proposal",
    )


@pytest.mark.parametrize(
    "mutation", ["order", "missing", "owner", "revision", "selection_hash"]
)
def test_bound_model_inputs_reject_swapped_or_partial_stage_context(mutation):
    raw = snapshot().model_dump()
    if mutation == "order":
        raw["inputs"] = tuple(reversed(raw["inputs"]))
    elif mutation == "missing":
        raw["inputs"] = raw["inputs"][:-1]
    elif mutation == "owner":
        raw["binding"]["owner_id"] = uuid4()
    elif mutation == "revision":
        raw["binding"]["input_revision_id"] = uuid4()
    else:
        raw["binding"]["input_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        ModelStageInputSnapshot.model_validate(raw)
