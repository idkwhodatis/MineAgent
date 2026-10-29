"""Actual immutable input-revision/claim construction, explicit writer doubles."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import rdagent_workflow_saved, rdagent_workflow_submission
from backend.app import research_workflow_rd_continuation as service
from backend.app.research_workflow_models import (
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import InputVersionRef
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.plan import RDGuidancePlan
from packages.research_workflow.rd_claim import WorkflowRDClaim
from tests.research_workflow.test_admission import Session
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_predecessor import chain  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.fixture
def continuation(chain, monkeypatch):  # noqa: F811
    c = chain
    root = c.b.root
    plan = RDGuidancePlan.model_validate_json(root.plan_canonical)
    root.status, root.blocked_reason = "blocked", "workflow_next_child_pending"
    root.current_stage_id, root.latest_checkpoint_id = c.feedback.id, c.feedbackrow.id
    root.budget_canonical = canonical_bytes(plan.budget_ceiling).decode()
    root.budget_sha256 = digest(plan.budget_ceiling)
    root.current_input_revision_id = uuid4()
    user_ref = InputVersionRef(
        origin="manual_user_submission",
        record_id=uuid4(),
        version_id=uuid4(),
        content_sha256="e" * 64,
    )
    old_feedback = c.projection.outputs[0].model_copy(update={"version_id": uuid4()})
    selection = WorkflowInputSnapshot(
        owner_id=root.owner_id,
        workflow_id=root.id,
        revision_id=root.current_input_revision_id,
        refs=(user_ref, old_feedback),
    )
    c.b.stage.allocation_canonical = canonical_bytes(
        plan.research_template.budget
    ).decode()
    c.b.stage.allocation_sha256 = digest(plan.research_template.budget)
    c.feedback.allocation_canonical = canonical_bytes(
        BudgetVector(storage_bytes=65536)
    ).decode()
    command = WorkflowRDClaim(
        workflow_id=root.id,
        expected_revision=root.revision,
        input_revision_id=selection.revision_id,
        idempotency_key="next-one",
    )
    monkeypatch.setattr(service, "require_schema", lambda *a: None)
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(service, "read_selected_inputs", lambda *a: selection)

    @contextmanager
    def guarded(*a, **kw):
        yield

    monkeypatch.setattr(service, "native_guard_from_publication", guarded)
    events, saved = [], {}
    monkeypatch.setattr(
        service,
        "verify_rd_predecessor",
        lambda *a: events.append("physical_predecessor"),
    )
    monkeypatch.setattr(
        service,
        "resolve_model_input_versions",
        lambda s, o, refs, settings: events.append(refs),
    )

    def writer(session, owner, workflow, stage_id, settings, *, admission):
        stage = next(
            row for row in session.added if isinstance(row, ResearchWorkflowStage)
        )
        assert (
            root.current_stage_id == stage.id
            and root.current_input_revision_id == stage.input_revision_id
        )
        assert session.flushed and events[0] == "physical_predecessor"
        row = NS(
            id=uuid4(),
            owner_id=owner,
            workflow_id=workflow,
            stage_id=stage_id,
            run_id=stage.child_run_id,
        )
        saved[row.id] = row
        events.append("new_child")
        return row

    monkeypatch.setattr(rdagent_workflow_submission, "submit_workflow_child", writer)
    monkeypatch.setattr(
        rdagent_workflow_saved,
        "verify_workflow_submission_under_native_guard",
        lambda s, o, r, settings: saved[r],
    )

    def session(prior=None):
        value = Session([root, prior, c.feedbackrow, 3, c.rdrow])
        value.scalars = lambda q: [c.b.stage, c.feedback]
        return value

    return NS(
        c=c,
        root=root,
        command=command,
        selection=selection,
        events=events,
        session=session,
    )


def test_next_child_new_revision_has_latest_user_refs_and_only_actual_feedback(
    continuation,
):
    c = continuation
    session = c.session()
    result = service.claim_next_rd_stage(
        session, c.root.owner_id, c.command, None, admission=object()
    )
    revision = next(
        row for row in session.added if isinstance(row, ResearchWorkflowRevision)
    )
    selected = WorkflowInputSnapshot.model_validate_json(revision.inputs_canonical)
    assert selected.refs == (c.selection.refs[0], c.c.projection.outputs[0])
    assert selected.revision_id != c.selection.revision_id
    assert c.events == ["physical_predecessor", selected.refs, "new_child"]
    original = session.added[-1]
    c.root.revision += 5
    c.root.status = "cancelled"
    retry = c.session(original)
    assert (
        service.claim_next_rd_stage(
            retry, c.root.owner_id, c.command, None, admission=object()
        )
        == result
    )
    assert not retry.added and c.events.count("new_child") == 1


@pytest.mark.parametrize(
    "change", ["cas", "cancel", "pause", "missing_checkpoint", "physical"]
)
def test_next_claim_races_and_missing_physical_evidence_fail_closed(
    continuation, monkeypatch, change
):
    c = continuation
    if change == "cas":
        c.root.revision += 1
    elif change in {"cancel", "pause"}:
        c.root.status = change + "_requested"
    elif change == "missing_checkpoint":
        c.c.feedbackrow.terminal_sha256 = "f" * 64
    else:

        def absent(*a):
            raise DomainError("workflow_waiting_containment", "Missing stop", 409)

        monkeypatch.setattr(service, "verify_rd_predecessor", absent)
    with pytest.raises(DomainError):
        service.claim_next_rd_stage(
            c.session(), c.root.owner_id, c.command, None, admission=object()
        )
    assert "new_child" not in c.events
