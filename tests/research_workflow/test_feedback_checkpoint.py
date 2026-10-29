"""Real checkpoint DTO/CAS with explicitly substituted physical boundary facts."""

from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from backend.app import research_input_feedback
from backend.app import research_workflow_feedback_checkpoint as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.contracts import WorkflowInputSnapshot
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_predecessor import chain  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.mark.parametrize(
    "state", ["running", "pause_requested", "cancel_requested", "stale", "replay"]
)
def test_feedback_checkpoint_requires_actual_terminal_and_cas(
    chain,  # noqa: F811
    monkeypatch,
    state,
):
    c = chain
    root = c.b.root
    root.current_stage_id = c.feedback.id
    root.status = (
        state if state in {"pause_requested", "cancel_requested"} else "running"
    )
    root.revision = 2 if state == "stale" else 1
    payload = c.projection
    selection = WorkflowInputSnapshot(
        owner_id=root.owner_id,
        workflow_id=root.id,
        revision_id=c.feedback.input_revision_id,
    )
    revision = NS(
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    rows = iter((root, c.feedbackrow if state == "replay" else None, revision))
    added = []
    session = NS(scalar=lambda q: next(rows), add=added.append, flush=lambda: None)
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        service,
        "_binding",
        lambda *a: (
            root,
            c.feedback,
            None,
            NS(source=payload.source),
            c.rdrow,
            c.facts,
        ),
    )
    facts = NS(
        derivation_id=payload.derivation_id,
        terminal_event_id=payload.terminal_event_id,
        terminal_event_sha256=payload.terminal_event_sha256,
        state="succeeded",
        outputs=payload.outputs,
        objects=payload.objects,
        exposure_ids=payload.exposure_ids,
    )
    monkeypatch.setattr(
        research_input_feedback, "validate_feedback_terminal", lambda *a, **kw: facts
    )
    if state == "stale":
        with pytest.raises(DomainError):
            service.observe_feedback_terminal(
                session,
                root.owner_id,
                root.id,
                c.feedback.id,
                1,
                c.b.t.settings,
                admission=object(),
                prepared_read=object(),
            )
        assert not added
        return
    identity, result = service.observe_feedback_terminal(
        session,
        root.owner_id,
        root.id,
        c.feedback.id,
        1,
        c.b.t.settings,
        admission=object(),
        prepared_read=object(),
    )
    assert result == payload
    if state == "replay":
        assert identity == c.feedbackrow.id and not added and root.revision == 1
    else:
        assert len(added) == 1 and root.revision == 2
        assert (
            root.status
            == {
                "running": "blocked",
                "pause_requested": "paused",
                "cancel_requested": "cancelled",
            }[state]
        )
        if state == "running":
            assert root.blocked_reason == "workflow_next_child_pending"


@pytest.mark.parametrize(
    "status,guard_failure",
    [("succeeded", False), ("cancelled", False), ("succeeded", True)],
)
def test_factory_guard_owns_only_commit_before_ack(monkeypatch, status, guard_failure):
    from backend.app import (
        factor_services,
        research_input_history,
        research_workflow_feedback,
    )
    from backend.app.core import maintenance

    events = []

    @contextmanager
    def lifecycle(*a):
        yield

    @contextmanager
    def publication(*a, **kw):
        events.append("guard")
        yield object()
        if guard_failure:
            raise DomainError("quota", "Cannot commit", 409)
        events.append("commit")

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        def __call__(self):
            @contextmanager
            def session():
                yield NS()  # No outer commit method: an extra commit is a failure.

            return session()

    monkeypatch.setattr(maintenance, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(factor_services, "publication", publication)
    monkeypatch.setattr(
        research_input_history,
        "read_derivation_history",
        lambda *a: NS(terminal_event_id=object(), status=status),
    )

    def read(*a, **kw):
        events.append("released_read")
        return object()

    monkeypatch.setattr(research_workflow_feedback, "read_released_feedback", read)

    def observe(*a, **kw):
        assert (kw["prepared_read"] is None) == (status == "cancelled")
        events.append("observe")
        return "checkpoint"

    monkeypatch.setattr(service, "observe_feedback_terminal", observe)
    if guard_failure:
        with pytest.raises(DomainError):
            service.checkpoint_feedback_stage(
                Factory(),
                NS(storage_root="/tmp"),
                owner_id=1,
                workflow_id=2,
                stage_id=3,
                expected_revision=4,
            )
        assert "commit" not in events
    else:
        assert (
            service.checkpoint_feedback_stage(
                Factory(),
                NS(storage_root="/tmp"),
                owner_id=1,
                workflow_id=2,
                stage_id=3,
                expected_revision=4,
            )
            == "checkpoint"
        )
        events.append("ack")
        assert events[-2:] == ["commit", "ack"] and events.count("commit") == 1
    assert ("released_read" in events) == (status == "succeeded")
