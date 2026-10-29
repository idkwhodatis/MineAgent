"""Atomic claim transaction doubles; real queued writer tested by its owner."""

import sys
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_rd_claim as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.rd_claim import WorkflowRDClaim
from tests.research_workflow.test_admission import Session
from tests.research_workflow.test_plan import plan


@pytest.fixture
def graph(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def guarded(*a, **kw):
        yield

    monkeypatch.setattr(service, "native_guard_from_publication", guarded)
    p = plan()
    root = NS(
        owner_id=p.owner_id,
        id=uuid4(),
        revision=1,
        current_input_revision_id=uuid4(),
        current_stage_id=None,
        latest_checkpoint_id=None,
        status="blocked",
        blocked_reason="workflow_child_adapter_unavailable",
        mode=p.mode,
        plan_canonical=canonical_bytes(p).decode(),
        plan_sha256=p.sha256,
        budget_canonical=canonical_bytes(p.budget_ceiling).decode(),
        budget_sha256=digest(p.budget_ceiling),
    )
    selection = WorkflowInputSnapshot(
        owner_id=p.owner_id,
        workflow_id=root.id,
        revision_id=root.current_input_revision_id,
    )
    command = WorkflowRDClaim(
        workflow_id=root.id,
        expected_revision=1,
        input_revision_id=selection.revision_id,
        idempotency_key="claim-one",
    )
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(service, "read_selected_inputs", lambda *a: selection)
    monkeypatch.setattr(service, "resolve_model_input_versions", lambda *a: ())
    calls = []
    saved = {}

    def writer(session, owner, workflow, stage_id, settings, *, admission):
        stage = session.added[0]
        assert root.current_stage_id == stage_id and root.status == "running"
        assert stage.child_run_id is not None and session.flushed
        calls.append(stage.child_run_id)
        receipt = NS(
            id=uuid4(),
            owner_id=owner,
            workflow_id=workflow,
            stage_id=stage_id,
            run_id=stage.child_run_id,
        )
        saved[receipt.id] = receipt
        return receipt

    monkeypatch.setitem(
        sys.modules,
        "backend.app.rdagent_workflow_submission",
        NS(submit_workflow_child=writer),
    )
    monkeypatch.setitem(
        sys.modules,
        "backend.app.rdagent_workflow_saved",
        NS(
            verify_workflow_submission_under_native_guard=lambda session, owner, sid, settings: (
                saved[sid]
            )
        ),
    )
    return root, command, calls


def session_for(root, prior=None):
    session = Session([root, prior])
    session.scalars = lambda query: ()
    return session


def test_new_claim_allocates_stage_and_child_once_same_transaction(graph):
    root, command, calls = graph
    session = session_for(root)
    result = service.claim_initial_rd_stage(
        session, root.owner_id, command, None, admission=object()
    )
    assert result.child_run_id == calls[0] and len(calls) == 1
    assert len(session.added) == 2 and root.revision == 2
    assert (
        result.qualification == "queued_child_not_model_consumption_or_completed_output"
    )
    prior = session.added[-1]
    root.status = "cancelled"
    root.revision = 13
    retry = session_for(root, prior)
    assert (
        service.claim_initial_rd_stage(
            retry, root.owner_id, command, None, admission=object()
        )
        == result
    )
    assert len(calls) == 1 and not retry.added


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", 2),
        ("current_input_revision_id", uuid4()),
        ("status", "paused"),
        ("status", "cancel_requested"),
        ("status", "running"),
    ],
)
def test_race_or_pause_does_not_allocate_child(graph, field, value):
    root, command, calls = graph
    setattr(root, field, value)
    session = session_for(root)
    with pytest.raises(DomainError):
        service.claim_initial_rd_stage(
            session, root.owner_id, command, None, admission=object()
        )
    assert not calls and not session.added


def test_same_key_changed_input_rejected_before_new_writer(graph):
    root, command, calls = graph
    session = session_for(root)
    service.claim_initial_rd_stage(
        session, root.owner_id, command, None, admission=object()
    )
    changed = command.model_copy(update={"input_revision_id": uuid4()})
    with pytest.raises(DomainError, match="already|unavailable|changed"):
        service.claim_initial_rd_stage(
            session_for(root, session.added[-1]),
            root.owner_id,
            changed,
            None,
            admission=object(),
        )
    assert len(calls) == 1


@pytest.mark.parametrize("guard_fails", [False, True])
def test_dispatch_ack_follows_only_guard_commit(monkeypatch, guard_fails):
    from contextlib import contextmanager

    events = []
    session = NS()

    @contextmanager
    def lifecycle(*args):
        yield

    @contextmanager
    def opened():
        yield session

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        def __call__(self):
            return opened()

    @contextmanager
    def guard(*args, **kwargs):
        yield "admission"
        events.append("final_check")
        if guard_fails:
            raise RuntimeError("quota final check")
        events.append("sole_commit")

    monkeypatch.setattr(service, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(service, "publication", guard)
    monkeypatch.setattr(
        service,
        "claim_initial_rd_stage",
        lambda *a, **k: events.append("flush_child") or "receipt",
    )
    if guard_fails:
        with pytest.raises(RuntimeError):
            service.dispatch_initial_rd_stage(
                Factory(), NS(storage_root="root"), owner_id=uuid4(), command=object()
            )
        assert events == ["flush_child", "final_check"]
    else:
        assert (
            service.dispatch_initial_rd_stage(
                Factory(), NS(storage_root="root"), owner_id=uuid4(), command=object()
            )
            == "receipt"
        )
        events.append("ACK")
        assert events == ["flush_child", "final_check", "sole_commit", "ACK"]
