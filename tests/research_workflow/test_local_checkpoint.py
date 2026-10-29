"""Root transition doubles; physical quota/bytes validated by source tests."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_local_checkpoint as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import WorkflowInputSnapshot
from tests.research_workflow.test_plan import plan


class Session:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.added = []

    def scalar(self, query):
        return next(self.rows)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


@pytest.fixture
def graph(monkeypatch):
    p = plan()
    allocation = BudgetVector(storage_bytes=service.PUBLICATION_PEAK_BYTES)
    p = p.model_copy(
        update={
            "normalize_external_first": True,
            "budget_ceiling": p.budget_ceiling.add(allocation),
        }
    )
    workflow, stage_id, revision_id = uuid4(), uuid4(), uuid4()
    selection = WorkflowInputSnapshot(
        owner_id=p.owner_id, workflow_id=workflow, revision_id=revision_id
    )
    root = NS(
        id=workflow,
        owner_id=p.owner_id,
        revision=3,
        current_stage_id=stage_id,
        current_input_revision_id=uuid4(),
        plan_canonical=canonical_bytes(p).decode(),
        plan_sha256=p.sha256,
        mode="continuous",
        status="running",
        latest_checkpoint_id=None,
        blocked_reason=None,
    )
    stage = NS(
        id=stage_id,
        owner_id=p.owner_id,
        workflow_id=workflow,
        ordinal=1,
        kind="external_normalization",
        child_run_id=None,
        input_revision_id=revision_id,
        definition_sha256="a" * 64,
        allocation_canonical=canonical_bytes(allocation).decode(),
        allocation_sha256=digest(allocation),
    )
    revision = NS(
        id=revision_id,
        owner_id=p.owner_id,
        workflow_id=workflow,
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    facts = NS(
        derivation_id=uuid4(),
        terminal_event_id=uuid4(),
        terminal_event_sha256="b" * 64,
        state="rejected",
        outputs=(),
        objects=(NS(id=uuid4(), kind="raw_external", sha256="c" * 64, size_bytes=5),),
    )
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *args: None)
    calls = []
    monkeypatch.setattr(
        service,
        "validate_external_terminal",
        lambda *args, **kwargs: calls.append(kwargs["admission"]) or facts,
    )
    return root, stage, revision, facts, calls


def run(graph, prior=None, expected=3, consumption=None):
    root, stage, revision, _facts, _calls = graph
    session = Session([root, stage, prior, consumption, revision])
    result = service.observe_external_terminal(
        session,
        root.owner_id,
        root.id,
        stage.id,
        expected,
        NS(),
        admission="test-guard-double",
    )
    return session, result


def test_actual_rejection_closes_local_stage_without_child_or_refund(graph):
    session, (_, payload) = run(graph)
    assert graph[0].status == "blocked"
    assert graph[0].blocked_reason == "workflow_stage_failed"
    assert graph[0].revision == 4
    assert graph[4] == ["test-guard-double"]
    assert payload.state == "rejected"
    assert "settled" not in payload.model_dump()
    assert len(session.added) == 1


def test_exact_replay_reverifies_and_does_not_change_newer_root(graph):
    session, expected = run(graph)
    graph[0].revision = 9
    graph[0].status = "paused"
    retry, value = run(graph, prior=session.added[0], expected=3)
    assert value == expected and retry.added == []
    assert graph[0].revision == 9 and graph[0].status == "paused"
    assert len(graph[4]) == 2


def test_current_revision_conflict_precedes_fresh_reads(graph):
    with pytest.raises(DomainError):
        run(graph, expected=2)
    assert graph[4] == []


def test_local_stage_cannot_hide_actual_model_consumption(graph):
    with pytest.raises(DomainError):
        run(graph, consumption=uuid4())
    assert graph[4] == []


def test_cancel_request_waits_for_actual_local_terminal(graph):
    graph[0].status = "cancel_requested"
    run(graph)
    assert graph[0].status == "cancelled"
    assert graph[0].latest_checkpoint_id is not None


@pytest.mark.parametrize(
    "status, expected", [("running", "blocked"), ("pause_requested", "paused")]
)
def test_success_closes_local_publication_but_never_submits_child(
    graph, status, expected
):
    from packages.research_inputs.contracts import InputVersionRef

    root, stage, _revision, facts, _calls = graph
    root.status = status
    ref = InputVersionRef(
        origin="normalized_external_proposal",
        record_id=uuid4(),
        version_id=uuid4(),
        content_sha256="d" * 64,
    )
    facts.state = "succeeded"
    facts.outputs = (ref,)
    facts.objects += (
        NS(id=uuid4(), kind="normalized_external", sha256="d" * 64, size_bytes=32),
    )
    session, (_, payload) = run(graph)
    assert root.status == expected
    assert stage.child_run_id is None
    assert len(session.added) == 1
    assert payload.outputs == (ref,)
    if expected == "blocked":
        assert root.blocked_reason == "workflow_child_adapter_unavailable"


@pytest.mark.parametrize("guard_failure", [False, True])
def test_checkpoint_ack_only_after_single_guard_owned_commit(
    monkeypatch, guard_failure
):
    from contextlib import contextmanager

    events = []
    session = NS()
    guard_is_committing = False

    def commit():
        assert guard_is_committing, "No hidden outer logical commit permitted"
        events.append("commit")

    session.commit = commit

    @contextmanager
    def lifecycle(*args):
        yield

    @contextmanager
    def open_session():
        yield session

    @contextmanager
    def quota(actual_session, *args, **kwargs):
        nonlocal guard_is_committing
        assert actual_session is session
        assert kwargs == {"peak_bytes": 0, "peak_files": 0}
        events.append("guard_enter")
        yield "actual-guard-double"
        events.append("final_quota_check")
        if guard_failure:
            raise RuntimeError("final quota failure")
        guard_is_committing = True
        actual_session.commit()
        guard_is_committing = False
        events.append("guard_exit")

    class Factory:
        def __init__(self):
            self.kw = {"bind": "engine"}

        def __call__(self):
            return open_session()

    def observe(*args, **kwargs):
        assert kwargs["admission"] == "actual-guard-double"
        events.append("observe")
        return "checkpoint"

    monkeypatch.setattr(service, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(service, "publication", quota)
    monkeypatch.setattr(service, "observe_external_terminal", observe)

    def call():
        value = service.checkpoint_external_stage(
            Factory(),
            NS(storage_root="root"),
            owner_id=uuid4(),
            workflow_id=uuid4(),
            stage_id=uuid4(),
            expected_revision=3,
        )
        events.append("ack")
        return value

    if guard_failure:
        with pytest.raises(RuntimeError, match="final quota failure"):
            call()
        assert events == ["guard_enter", "observe", "final_quota_check"]
    else:
        assert call() == "checkpoint"
        assert events == [
            "guard_enter",
            "observe",
            "final_quota_check",
            "commit",
            "guard_exit",
            "ack",
        ]
