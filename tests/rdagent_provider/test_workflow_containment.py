"""Actual retained SDK receipts still required for workflow-profile terminal state."""

# ruff: noqa: F811
from types import SimpleNamespace as NS

import pytest
from test_attempt_containment import attempt_graph  # noqa: F401
from test_cold_receipts import graph  # noqa: F401

from backend.app import rdagent_provider_containment as barrier
from packages.rdagent_adapter.persisted_identity import LEGACY_ONLY_FIELDS
from packages.rdagent_adapter.steer import SteerSnapshot
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.model_inputs import ModelStageInputSnapshot
from tests.rdagent.test_rdagent_workflow_child_contracts import (
    config as workflow_config,
)


@pytest.fixture
def workflow_graph(attempt_graph):
    run, attempt, settings, inventory, files = attempt_graph
    previous = barrier.require_source_identity(run)
    template = workflow_config()
    selection = WorkflowInputSnapshot.model_validate(
        {**template.workflow_inputs.selection.model_dump(), "owner_id": run.owner_id}
    )
    inputs = ModelStageInputSnapshot.model_validate(
        {
            **template.workflow_inputs.model_dump(),
            "selection": selection,
            "binding": {
                **template.workflow_inputs.binding.model_dump(),
                "owner_id": run.owner_id,
                "input_sha256": selection.sha256,
            },
        }
    )
    empty = SteerSnapshot(owner_id=run.owner_id)
    config = WorkflowFactorResearchConfig.model_validate(
        {
            **template.model_dump(),
            "workflow_child_run_id": run.id,
            "workflow_inputs": inputs,
            "workflow_inputs_sha256": inputs.sha256,
            "steer_snapshot": empty,
            "steer_snapshot_sha256": empty.sha256,
            "model_binding": previous.model_binding,
        }
    )
    for field in LEGACY_ONLY_FIELDS:
        setattr(run, field, None)
    run.config = config.model_dump(mode="json")
    run.purpose, run.engine = "factor_research", "rdagent"
    run.dataset_version_id, run.request_sha256 = (
        config.input.dataset.dataset_version_id,
        config.sha256,
    )
    run.research_image_digest = config.runtime.orchestrator_image
    session = NS(scalars=lambda query: NS(all=lambda: ["029_research_workflow"]))
    return session, run, attempt, settings, inventory, files


def test_workflow_completed_sdk_full_charge_and_positive_stop_is_required(
    workflow_graph,
):
    session, run, attempt, settings, _, _ = workflow_graph
    assert barrier.provider_attempt_stopped(session, run, attempt, settings)


@pytest.mark.parametrize(
    "missing", ["intent", "started", "stopped", "charge", "bytes", "schema"]
)
def test_workflow_profile_cannot_use_historical_fixture_stop_shortcut(
    workflow_graph, missing
):
    session, run, attempt, settings, inventory, files = workflow_graph
    if missing in {"intent", "started", "stopped"}:
        inventory["RDProviderTransportReceipt"] = [
            r for r in inventory["RDProviderTransportReceipt"] if r["kind"] != missing
        ]
    elif missing == "charge":
        inventory["ResearchBudgetSettlement"].clear()
    elif missing == "bytes":
        (settings.storage_root / next(iter(files))).unlink()
    else:
        session = NS(scalars=lambda query: NS(all=lambda: ["028_oe_structured_search"]))
    assert not barrier.provider_attempt_stopped(session, run, attempt, settings)
