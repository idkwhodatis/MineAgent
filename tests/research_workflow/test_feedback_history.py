"""Feedback audit uses exact original terminal, not external-normalization grammar."""

from types import SimpleNamespace as NS

import pytest

from backend.app import research_input_feedback
from backend.app import research_workflow_history as service
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.contracts import WorkflowInputSnapshot
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_predecessor import chain  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.mark.parametrize("change", [None, "checkpoint_digest", "release"])
def test_feedback_history_rechecks_retained_terminal_and_exposures(
    chain,  # noqa: F811
    monkeypatch,
    tmp_path,
    change,
):
    c = chain
    stage, root, stored = c.feedback, c.b.root, c.projection
    stage.child_run_id = None
    selected = WorkflowInputSnapshot(
        owner_id=root.owner_id, workflow_id=root.id, revision_id=stage.input_revision_id
    )
    revision = NS(
        owner_id=root.owner_id,
        workflow_id=root.id,
        id=stage.input_revision_id,
        inputs_canonical=canonical_bytes(selected).decode(),
        inputs_sha256=selected.sha256,
    )
    derivation = NS(id=stored.derivation_id)
    history = NS(
        derivation_id=stored.derivation_id,
        terminal_event_id=stored.terminal_event_id,
        terminal_event_sha256=stored.terminal_event_sha256,
        status="succeeded",
        outputs=stored.outputs,
        started_at=None,
        terminal_at=None,
        error_code=None,
    )
    rows = iter((root, stage, revision, derivation, c.feedbackrow))
    session = NS(scalar=lambda q: next(rows))
    monkeypatch.setattr(service, "require_schema", lambda *a: None)
    monkeypatch.setattr(service, "verify_model_input_versions", lambda *a: None)
    monkeypatch.setattr(service, "read_derivation_history", lambda *a: history)
    monkeypatch.setattr(
        research_input_feedback, "verify_feedback_derivation", lambda *a: None
    )

    def actual(*a):
        require_native_storage_guard(tmp_path)
        if change == "release":
            raise DomainError("workflow_feedback_release_required", "No ledger", 409)
        return NS(
            derivation_id=stored.derivation_id,
            terminal_event_id=stored.terminal_event_id,
            terminal_event_sha256=stored.terminal_event_sha256,
            state="succeeded",
            outputs=stored.outputs,
            objects=stored.objects,
            exposure_ids=stored.exposure_ids,
        )

    monkeypatch.setattr(
        research_input_feedback, "validate_feedback_terminal_under_native_guard", actual
    )
    (tmp_path / "factors").mkdir()
    if change == "checkpoint_digest":
        c.feedbackrow.terminal_sha256 = "f" * 64

    def read():
        return service.read_stage_history(
            session, root.owner_id, root.id, stage.id, NS(storage_root=tmp_path)
        )

    if change:
        with pytest.raises(DomainError):
            read()
    else:
        result = read()
        assert result.derived_input_refs == stored.outputs
        assert result.consumption == "not_submitted_to_model"
        assert result.checkpoint_id == c.feedbackrow.id
