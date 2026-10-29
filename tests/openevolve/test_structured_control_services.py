"""Real normal declaration codecs with DB doubles; no containment/PG claim."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import oe_structured_control_services as control
from backend.app.models import ExecutionAttempt
from backend.app.services import DomainError
from tests.openevolve.test_structured_run_views import view_graph


@pytest.fixture
def state(monkeypatch):
    session, run, declaration, _ = view_graph()
    scalar = session.scalar
    state = SimpleNamespace(
        session=session,
        run=run,
        declaration=declaration,
        revisions=[control.REVISION],
        attempt=None,
        events=[],
        locks=[],
        flushes=[],
    )
    session.scalars = lambda _: SimpleNamespace(all=lambda: state.revisions)
    session.scalar = lambda query: (
        state.attempt
        if query.column_descriptions[0]["entity"] is ExecutionAttempt
        else scalar(query)
    )
    session.flush = lambda: state.flushes.append(True)
    monkeypatch.setattr(
        control, "_try_neutral_lock", lambda s, key: state.locks.append(key)
    )
    monkeypatch.setattr(
        control,
        "append_event",
        lambda s, r, name, payload, aid: state.events.append((name, payload, aid)),
    )
    return state


def test_queued_cancel_without_attempt_is_flush_only(state):
    result = control.request_cancel(state.session, state.run.owner_id, state.run.id)
    assert result.status == "cancelled" and result.pending is False
    assert state.locks == [f"submit:{state.run.owner_id}"]
    assert state.events == [
        ("oe_structured_cancelled_before_claim", {"status": "cancelled"}, None)
    ]
    assert state.flushes == [True]
    assert not hasattr(result, "sandbox_stopped")


@pytest.mark.parametrize("status", ["preparing", "running", "finalizing"])
def test_active_cancel_does_not_claim_stop_or_reset_attempt(state, status):
    state.run.status = status
    state.run.active_attempt_id = uuid4()
    old = state.run.active_attempt_id
    result = control.request_cancel(state.session, state.run.owner_id, state.run.id)
    assert result.pending and result.status == "cancel_requested"
    assert state.run.active_attempt_id == old
    assert state.events[0][1]["physical_stop_proven"] is False
    assert control.controls_for(state.session, state.run).cancel is False


@pytest.mark.parametrize(
    "status", ["cancel_requested", "succeeded", "failed", "cancelled", "interrupted"]
)
def test_repeated_or_terminal_request_preserves_evidence(state, status):
    state.run.status = status
    old = state.run.updated_at
    result = control.request_cancel(state.session, state.run.owner_id, state.run.id)
    assert result.status == status and result.pending == (status == "cancel_requested")
    assert state.run.updated_at == old and state.events == []


@pytest.mark.parametrize("kind", ["active_pointer", "retained_attempt"])
def test_queued_with_any_attempt_is_not_cancelled_before_claim(state, kind):
    if kind == "active_pointer":
        state.run.active_attempt_id = uuid4()
    else:
        state.attempt = uuid4()
    with pytest.raises(DomainError, match="Queued execution state"):
        control.request_cancel(state.session, state.run.owner_id, state.run.id)
    assert state.run.status == "queued" and not state.events and not state.flushes


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["026_rd_seeded_submissions"],
        ["029_unknown"],
        [control.REVISION, control.REVISION],
    ],
)
def test_exact_supported_revisions_only_no_old_or_future_alias(state, revisions):
    state.revisions = revisions
    assert not control.controls_for(state.session, state.run).cancel
    with pytest.raises(DomainError):
        control.request_cancel(state.session, state.run.owner_id, state.run.id)
    assert not state.locks and not state.events


@pytest.mark.parametrize(
    "change",
    ["owner", "id", "legacy", "receipt_sha", "receipt_origin", "paused", "unknown"],
)
def test_foreign_or_mutated_declaration_never_mutates_control(state, change):
    owner, rid = state.run.owner_id, state.run.id
    if change == "owner":
        owner = uuid4()
    elif change == "id":
        rid = uuid4()
    elif change == "legacy":
        state.run.engine = "legacy"
    elif change == "receipt_sha":
        state.declaration.request_sha256 = "0" * 64
    elif change == "receipt_origin":
        state.declaration.origin = "agent_proposal"
    elif change == "paused":
        state.run.status = "paused"
    else:
        state.run.status = "invented"
    with pytest.raises(DomainError):
        control.request_cancel(state.session, owner, rid)
    assert not state.events and not state.flushes


def test_actions_only_offer_cancel_for_authenticated_normal_run(state):
    value = control.controls_for(state.session, state.run)
    assert value.cancel and not value.pause and not value.resume
    assert value.reason_code == "structured_pause_resume_not_supported"


def test_028_inherits_normal_queued_cancel(state):
    state.revisions = ["029_research_workflow"]
    result = control.request_cancel(state.session, state.run.owner_id, state.run.id)
    assert result.status == "cancelled" and result.pending is False
    assert state.flushes == [True]
