"""New control requests still require fresh physical checkpoint replay."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_control as control
from backend.app import research_workflow_rd_checkpoint as service
from backend.app.services import DomainError
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.mark.parametrize("status", ["pause_requested", "cancel_requested"])
@pytest.mark.parametrize("missing", [False, True])
def test_existing_checkpoint_does_not_skip_new_control_or_physical_proof(
    boundary,  # noqa: F811
    monkeypatch,
    status,
    missing,
):
    b = boundary
    prior = NS(id=uuid4())
    b.root.status = status
    b.root.latest_checkpoint_id = prior.id
    b.root.current_stage_id = b.stage.id
    rows = iter((b.root, b.stage, prior))
    b.session.scalar = lambda q: next(rows)

    def verify(*a):
        if missing:
            raise DomainError("workflow_waiting_containment", "No SDK stop", 409)
        return prior, b.actual

    monkeypatch.setattr(service, "verify_rd_checkpoint", verify)
    if missing:
        with pytest.raises(DomainError):
            service.observe_rd_terminal(
                b.session, b.root.owner_id, b.root.id, b.stage.id, 1, b.t.settings
            )
        assert b.root.status == status and b.root.revision == 1
    else:
        assert service.observe_rd_terminal(
            b.session, b.root.owner_id, b.root.id, b.stage.id, 1, b.t.settings
        ) == (prior, b.actual)
        assert b.root.status == (
            "paused" if status == "pause_requested" else "cancelled"
        )
        assert b.root.revision == 2 and not b.added


def test_old_stage_checkpoint_cannot_acknowledge_new_current_stage_control():
    root = NS(current_stage_id=uuid4(), status="cancel_requested", revision=7)
    assert not control.acknowledge_verified_checkpoint_control(
        root, uuid4(), uuid4(), 7
    )
    assert root.status == "cancel_requested" and root.revision == 7
