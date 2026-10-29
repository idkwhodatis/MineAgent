"""Actual row allocation/CAS graph doubles, not feedback computation."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_feedback_stage as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.mark.parametrize(
    "change", [None, "cas", "paused", "wrong_child", "missing_checkpoint"]
)
def test_feedback_stage_only_from_own_committed_rd_boundary(
    boundary,  # noqa: F811
    monkeypatch,
    change,
):
    b = boundary
    from packages.research_workflow.plan import RDGuidancePlan

    p = RDGuidancePlan.model_validate_json(b.root.plan_canonical)
    b.stage.child_run_id = b.actual.child_run_id
    b.stage.allocation_canonical = canonical_bytes(p.research_template.budget).decode()
    b.stage.allocation_sha256 = digest(p.research_template.budget)
    b.root.current_input_revision_id = uuid4()
    b.root.status = "blocked"
    b.root.blocked_reason = "workflow_feedback_pending"
    b.root.latest_checkpoint_id = uuid4()
    facts = b.actual.model_copy(
        update={
            "owner_id": b.root.owner_id,
            "definition_sha256": b.stage.definition_sha256,
        }
    )
    checkpoint = NS(
        id=b.root.latest_checkpoint_id,
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        stage_id=b.stage.id,
        terminal_canonical=canonical_bytes(facts).decode(),
        terminal_sha256=digest(facts),
    )
    if change == "cas":
        b.root.revision = 3
    elif change == "paused":
        b.root.status = "paused"
    elif change == "wrong_child":
        b.stage.child_run_id = uuid4()
    elif change == "missing_checkpoint":
        checkpoint = None
    rows = iter((b.root, checkpoint))
    written = []
    session = NS(
        scalar=lambda q: next(rows),
        scalars=lambda q: [b.stage],
        add=written.append,
        flush=lambda: None,
    )
    monkeypatch.setattr(service, "require_schema", lambda *a: None)
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    if change:
        with pytest.raises(DomainError):
            service.allocate_feedback_stage(
                session, b.root.owner_id, b.root.id, 1, b.t.settings, admission=object()
            )
        assert not written
    else:
        stage = service.allocate_feedback_stage(
            session, b.root.owner_id, b.root.id, 1, b.t.settings, admission=object()
        )
        assert (
            stage.kind == "experiment_feedback_projection"
            and stage.child_run_id is None
        )
        assert (
            b.root.current_stage_id == stage.id
            and b.root.status == "ready"
            and len(written) == 1
        )
