"""Explicit workflow-origin data cannot masquerade as a legacy user child."""

from uuid import uuid4

import pytest

from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_workflow.contracts import (
    StageExecutionBinding,
    WorkflowInputSnapshot,
)
from packages.research_workflow.model_inputs import ModelStageInputSnapshot
from tests.openevolve.test_structured_persisted_identity import graph


def config():
    _, run, _, _, _, _ = graph()
    raw = run.config.copy()
    fixed = raw["fixed_inputs"]
    selection = WorkflowInputSnapshot(
        owner_id=run.owner_id, workflow_id=uuid4(), revision_id=uuid4()
    )
    binding = StageExecutionBinding(
        owner_id=run.owner_id,
        workflow_id=selection.workflow_id,
        stage_id=uuid4(),
        input_revision_id=selection.revision_id,
        input_sha256=selection.sha256,
        definition_sha256="a" * 64,
    )
    inputs = ModelStageInputSnapshot(binding=binding, selection=selection)
    raw["schema_version"] = "mineagent.oe-structured-provider-run.v2"
    raw["workflow_request_sha256"] = raw.pop("authenticated_request_sha256")
    raw["workflow_context"] = {
        "definition_sha256": "a" * 64,
        "child_run_id": str(run.id),
        "inputs": inputs.model_dump(mode="json"),
        "rd_stage_id": str(uuid4()),
        "rd_checkpoint_id": str(uuid4()),
        "rd_checkpoint_sha256": "b" * 64,
        "source_run_id": str(uuid4()),
        "source_attempt_id": str(uuid4()),
        "source_generation": 1,
        "snapshot_id": fixed["snapshot_id"],
        "snapshot_manifest_sha256": fixed["snapshot_manifest_sha256"],
        "factor_version_id": fixed["factor_version_id"],
        "analysis_id": str(uuid4()),
        "analysis_result_sha256": "c" * 64,
        "release_exposure_id": fixed["release_receipt_ids"][0],
        "release_provenance_sha256": "d" * 64,
    }
    return raw


def test_new_context_is_retained_not_a_new_authenticated_user_request():
    raw = config()
    value = WorkflowStructuredRunConfig.model_validate_json(canonical_bytes(raw))
    assert "authenticated_request_sha256" not in value.model_dump()
    with pytest.raises(ValueError):
        ProviderStructuredRunConfig.model_validate_json(canonical_bytes(value))


@pytest.mark.parametrize(
    "field",
    [
        "definition_sha256",
        "child_run_id",
        "snapshot_id",
        "snapshot_manifest_sha256",
        "factor_version_id",
        "release_exposure_id",
    ],
)
def test_bound_context_cannot_be_swapped(field):
    raw = config()
    raw["workflow_context"][field] = (
        "f" * 64 if field.endswith("sha256") else str(uuid4())
    )
    with pytest.raises(ValueError):
        WorkflowStructuredRunConfig.model_validate_json(canonical_bytes(raw))


def test_previous_rd_stage_cannot_alias_current_stage():
    raw = config()
    raw["workflow_context"]["rd_stage_id"] = raw["workflow_context"]["inputs"][
        "binding"
    ]["stage_id"]
    with pytest.raises(ValueError):
        WorkflowStructuredRunConfig.model_validate_json(canonical_bytes(raw))
