"""Definition grammar only; actual child writer must resolve every referenced row."""

from uuid import uuid4

import pytest

from packages.research_workflow.rd_stage import RDStageDefinition


def test_initial_parent_does_not_invent_workflow_source():
    value = RDStageDefinition(
        plan_sha256="a" * 64,
        research_iteration=0,
        predecessor_checkpoint_id=None,
        parent_factor_version_id=uuid4(),
        parent_source_stage_id=None,
    )
    assert value.parent_factor_version_id is not None
    with pytest.raises(ValueError):
        RDStageDefinition.model_validate(
            {**value.model_dump(), "parent_source_stage_id": uuid4()}
        )


@pytest.mark.parametrize(
    "field",
    ["predecessor_checkpoint_id", "parent_factor_version_id", "parent_source_stage_id"],
)
def test_next_child_cannot_omit_actual_parent_checkpoint_binding(field):
    raw = {
        "plan_sha256": "a" * 64,
        "research_iteration": 1,
        "predecessor_checkpoint_id": uuid4(),
        "parent_factor_version_id": uuid4(),
        "parent_source_stage_id": uuid4(),
    }
    assert RDStageDefinition.model_validate(raw).research_iteration == 1
    raw[field] = None
    with pytest.raises(ValueError):
        RDStageDefinition.model_validate(raw)
