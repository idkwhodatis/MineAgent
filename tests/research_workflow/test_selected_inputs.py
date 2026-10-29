from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_history as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.contracts import WorkflowInputSnapshot


@pytest.mark.parametrize("changed", [False, True])
def test_current_selection_is_exact_future_revision_not_stage(monkeypatch, changed):
    owner, workflow, revision_id = uuid4(), uuid4(), uuid4()
    value = WorkflowInputSnapshot(
        owner_id=owner, workflow_id=workflow, revision_id=revision_id
    )
    root = NS(
        owner_id=owner,
        id=workflow,
        current_input_revision_id=revision_id,
        current_stage_id=uuid4(),
    )
    revision = NS(
        owner_id=owner,
        workflow_id=workflow,
        id=revision_id,
        inputs_canonical=canonical_bytes(value).decode(),
        inputs_sha256="f" * 64 if changed else value.sha256,
    )
    rows = iter([root, revision])
    session = NS(scalar=lambda query: next(rows))
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    if changed:
        with pytest.raises(DomainError):
            service.read_selected_inputs(session, owner, workflow)
    else:
        assert service.read_selected_inputs(session, owner, workflow) == value
