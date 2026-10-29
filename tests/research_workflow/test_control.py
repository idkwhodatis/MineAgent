"""Transaction-decision unit tests, not PostgreSQL race/worker acceptance."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import research_workflow_control as service
from backend.app.services import DomainError
from packages.research_workflow.contracts import StageConsumption, WorkflowCommand


class Session:
    def __init__(self, values):
        self.values = iter(values)
        self.added = []
        self.flushed = False

    def scalar(self, statement):
        return next(self.values)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushed = True


@pytest.fixture
def prepared(monkeypatch):
    owner, workflow = uuid4(), uuid4()
    root = SimpleNamespace(
        id=workflow,
        owner_id=owner,
        revision=1,
        mode="continuous",
        status="ready",
        current_stage_id=None,
        latest_checkpoint_id=None,
        current_input_revision_id=uuid4(),
    )
    monkeypatch.setattr(service, "require_schema", lambda session: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda session, key: None)
    return owner, root


def command(root, action="pause_after_stage", revision=1, key="request-1"):
    return WorkflowCommand(
        workflow_id=root.id,
        expected_revision=revision,
        idempotency_key=key,
        action=action,
    )


def test_initial_pause_and_resume_do_not_invent_stage_or_stop(prepared):
    owner, root = prepared
    session = Session([root, None, None])
    receipt = service.request_control(session, owner, command(root))
    assert receipt.status == "paused" and receipt.checkpoint_id is None
    assert receipt.current_stage_id is None and root.revision == 2
    assert root.mode == "continuous"
    assert session.flushed and len(session.added) == 1
    resumed = service.request_control(
        Session([root, None, None]), owner, command(root, "resume", 2, "request-2")
    )
    assert resumed.status == "ready" and resumed.revision == 3


def test_running_pause_is_only_pending_and_inputs_unchanged(prepared):
    owner, root = prepared
    root.status, root.current_stage_id = "running", uuid4()
    frozen = root.current_input_revision_id
    receipt = service.request_control(
        Session([root, None, root.current_stage_id]), owner, command(root)
    )
    assert receipt.status == "pause_requested" and receipt.checkpoint_id is None
    assert root.current_input_revision_id == frozen


def test_cancel_running_does_not_claim_positive_stop(prepared):
    owner, root = prepared
    root.status, root.current_stage_id = "running", uuid4()
    result = service.request_control(
        Session([root, None, root.current_stage_id]), owner, command(root, "cancel")
    )
    assert result.status == "cancel_requested"
    assert result.checkpoint_id is None


def test_exact_retry_returns_original_receipt_after_revision_changes(prepared):
    owner, root = prepared
    request = command(root)
    session = Session([root, None, None])
    original = service.request_control(session, owner, request)
    root.revision = 10
    replay = Session([root, session.added[0]])
    assert service.request_control(replay, owner, request) == original
    assert not replay.added and not replay.flushed


def test_changed_idempotency_input_rejected(prepared):
    owner, root = prepared
    session = Session([root, None, None])
    service.request_control(session, owner, command(root))
    with pytest.raises(DomainError, match="different immutable input"):
        service.request_control(
            Session([root, session.added[0]]), owner, command(root, "cancel")
        )


def test_stale_revision_loses_without_mutation(prepared):
    owner, root = prepared
    root.revision = 2
    session = Session([root, None])
    with pytest.raises(DomainError, match="reload"):
        service.request_control(session, owner, command(root))
    assert root.status == "ready" and not session.added


def test_actual_stage_resume_without_guard_remains_closed(prepared):
    owner, root = prepared
    root.status, root.current_stage_id = "paused", uuid4()
    root.latest_checkpoint_id = uuid4()
    with pytest.raises(DomainError, match="checkpoint guard required"):
        service.request_control(
            Session([root, None, root.current_stage_id]), owner, command(root, "resume")
        )
    assert root.revision == 1


def test_foreign_owner_lookup_returns_not_found(prepared):
    _, root = prepared
    with pytest.raises(DomainError, match="not found"):
        service.request_control(Session([None]), uuid4(), command(root))


@pytest.mark.parametrize("state", ["selected_not_submitted", "not_consumed_numeric"])
def test_selection_or_numeric_cannot_claim_actual_model_request(state):
    with pytest.raises(ValueError, match="actual submitted model request"):
        StageConsumption(
            stage_id=uuid4(),
            input_revision_id=uuid4(),
            input_sha256="a" * 64,
            state=state,
            model_operation_id=uuid4(),
            request_sha256="b" * 64,
        )


def test_inclusion_needs_actual_operation_and_request_hash():
    with pytest.raises(ValueError, match="actual submitted model request"):
        StageConsumption(
            stage_id=uuid4(),
            input_revision_id=uuid4(),
            input_sha256="a" * 64,
            state="included_in_submitted_prompt",
            model_operation_id=None,
            request_sha256=None,
        )
