"""Workflow queue drift classification with actual canonical request validation."""

from types import SimpleNamespace as NS

import pytest

from backend.app import oe_workflow_queue as queue
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from tests.openevolve.test_structured_provider_submission import request
from tests.openevolve.test_workflow_run import config as workflow_config


@pytest.fixture
def binding(monkeypatch):
    config = WorkflowStructuredRunConfig.model_validate_json(
        canonical_bytes(workflow_config())
    )
    raw = request().model_dump()
    raw.update(
        schema_version="mineagent.oe-structured-workflow-submission.v1",
        profile="workflow_fixed_factor_operator_closed_program_v1",
        snapshot_id=config.workflow_context.snapshot_id,
        workflow_context=config.workflow_context,
    )
    command = WorkflowStructuredSearchRequest.model_validate(raw)
    config = config.model_copy(update={"workflow_request_sha256": command.sha256})
    row = NS(
        origin="workflow_stage",
        request_canonical=canonical_bytes(command).decode(),
        request_sha256=command.sha256,
        preflight_sha256="a" * 64,
    )
    run = NS(owner_id=config.search.owner_id)
    current = NS(runtime=config.runtime, model_binding=config.model_binding)
    monkeypatch.setattr(queue, "require_structured_workflow_schema", lambda s: None)
    monkeypatch.setattr(queue, "resolve_runtime", lambda s: current)
    monkeypatch.setattr(
        queue, "resolve_operator_profile", lambda *a: (None, current.model_binding)
    )
    return NS(config=config, command=command, row=row, run=run, current=current)


@pytest.mark.parametrize("field", ["runtime", "model_binding"])
def test_only_current_profile_drift_is_deferred(binding, field):
    c = binding
    assert queue.dispatch_binding(None, c.run, c.row, c.config, None)[0] == c.command
    setattr(c.current, field, object())
    with pytest.raises(DomainError) as error:
        queue.dispatch_binding(None, c.run, c.row, c.config, None)
    assert error.value.code == "oe_structured_queued_profile_changed"


@pytest.mark.parametrize("field", ["origin", "request_canonical", "request_sha256"])
def test_corrupt_saved_binding_is_not_classified_as_current_profile_drift(
    binding, field
):
    c = binding
    if field == "request_canonical":
        c.row.request_canonical += " "
    else:
        setattr(c.row, field, "foreign")
    with pytest.raises(DomainError) as error:
        queue.dispatch_binding(None, c.run, c.row, c.config, None)
    assert error.value.code == "workflow_oe_queued_binding_changed"
