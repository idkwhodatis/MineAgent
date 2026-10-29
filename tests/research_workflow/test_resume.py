"""Safe boundary resume is distinct from acknowledging a pause request."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_control
from backend.app import research_workflow_resume as service
from backend.app.services import DomainError
from tests.research_workflow.test_control import Session, command, prepared  # noqa: F401
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_predecessor import chain  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.mark.parametrize("missing", [False, True])
def test_resume_only_after_fixed_physical_verifier(prepared, monkeypatch, missing):  # noqa: F811
    owner, root = prepared
    root.status, root.current_stage_id, root.latest_checkpoint_id = (
        "paused",
        uuid4(),
        uuid4(),
    )
    original_revision = root.current_input_revision_id

    def verify(*a, **kw):
        assert kw["admission"] is admission
        if missing:
            raise DomainError(
                "workflow_waiting_containment", "Missing actual stop", 409
            )
        return "workflow_next_child_pending"

    monkeypatch.setattr(service, "verify_resume_checkpoint", verify)
    admission = object()
    request = command(root, "resume")
    session = Session([root, None, root.current_stage_id])
    if missing:
        with pytest.raises(DomainError):
            research_workflow_control.request_control(
                session, owner, request, settings=object(), admission=admission
            )
        assert root.status == "paused" and not session.added
    else:
        result = research_workflow_control.request_control(
            session, owner, request, settings=object(), admission=admission
        )
        assert (
            result.status == "blocked"
            and root.blocked_reason == "workflow_next_child_pending"
        )
        assert root.current_input_revision_id == original_revision
        root.revision += 9
        assert (
            research_workflow_control.request_control(
                Session([root, session.added[0]]), owner, request
            )
            == result
        )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing_stop",
        "changed_checkpoint",
        "wrong_prior",
        "wrong_source",
        "wrong_revision",
    ],
)
def test_feedback_resume_checks_original_physical_terminal(chain, monkeypatch, change):  # noqa: F811
    from backend.app import research_input_feedback, research_workflow_native_guard

    c = chain
    root = c.b.root
    root.current_stage_id, root.latest_checkpoint_id = c.feedback.id, c.feedbackrow.id
    values = iter((c.feedback, c.feedbackrow))
    session = NS(scalar=lambda q: next(values))
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)

    @contextmanager
    def borrowed(*a, **kw):
        yield

    monkeypatch.setattr(
        research_workflow_native_guard, "native_guard_from_publication", borrowed
    )
    stored = c.projection
    from backend.app import research_workflow_feedback

    monkeypatch.setattr(
        research_workflow_feedback,
        "_binding",
        lambda *a: (root, c.feedback, None, NS(source=stored.source), c.rdrow, None),
    )

    def actual(*a):
        if change == "missing_stop":
            raise DomainError(
                "workflow_waiting_containment", "Missing physical stop", 409
            )
        return NS(
            state="succeeded",
            derivation_id=stored.derivation_id,
            terminal_event_id=stored.terminal_event_id,
            terminal_event_sha256=stored.terminal_event_sha256,
            outputs=stored.outputs,
            objects=stored.objects,
            exposure_ids=stored.exposure_ids,
        )

    monkeypatch.setattr(
        research_input_feedback, "validate_feedback_terminal_under_native_guard", actual
    )
    if change in {"wrong_prior", "wrong_source", "wrong_revision"}:
        from packages.factors.contracts import canonical_bytes, digest

        updates = {
            "wrong_prior": {"rd_checkpoint_id": uuid4()},
            "wrong_source": {
                "source": stored.source.model_copy(update={"output_id": uuid4()})
            },
            "wrong_revision": {"input_revision_id": uuid4()},
        }[change]
        changed = stored.model_copy(update=updates)
        c.feedbackrow.terminal_canonical = canonical_bytes(changed).decode()
        c.feedbackrow.terminal_sha256 = digest(changed)
    if change == "changed_checkpoint":
        c.feedbackrow.terminal_sha256 = "f" * 64
    if change:
        with pytest.raises(DomainError):
            service.verify_resume_checkpoint(
                session, root.owner_id, root, None, admission=object()
            )
    else:
        assert (
            service.verify_resume_checkpoint(
                session, root.owner_id, root, None, admission=object()
            )
            == "workflow_next_child_pending"
        )
