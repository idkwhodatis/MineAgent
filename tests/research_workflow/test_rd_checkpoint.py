"""Root checkpoint CAS tests with real terminal DTO and explicit physical doubles."""

from types import SimpleNamespace as NS

import pytest

from backend.app import research_workflow_rd_checkpoint as service
from backend.app import research_workflow_rd_terminal
from backend.app.research_workflow_models import ResearchWorkflowCheckpoint
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.rd_stage import RDStageDefinition
from tests.research_workflow.test_plan import plan
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.fixture
def boundary(completed_graph, monkeypatch):  # noqa: F811
    t = completed_graph
    actual = research_workflow_rd_terminal.read_rd_terminal_in_session(
        t.session, t.owner, t.workflow, t.stage, t.settings
    )
    p = plan()
    definition = RDStageDefinition(
        plan_sha256=p.sha256,
        research_iteration=0,
        predecessor_checkpoint_id=None,
        parent_factor_version_id=p.initial_parent_factor_version_id,
        parent_source_stage_id=None,
    )
    root = NS(
        owner_id=p.owner_id,
        id=t.workflow,
        revision=1,
        current_stage_id=t.stage,
        status="running",
        mode=p.mode,
        plan_canonical=canonical_bytes(p).decode(),
        plan_sha256=p.sha256,
        latest_checkpoint_id=None,
    )
    stage = NS(
        owner_id=p.owner_id,
        workflow_id=t.workflow,
        id=t.stage,
        kind="rd_factor_generation",
        ordinal=1,
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=definition.sha256,
    )
    added = []
    values = iter((root, stage, None))
    session = NS(scalar=lambda q: next(values), add=added.append, flush=lambda: None)
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    monkeypatch.setattr(service, "require_native_storage_guard", lambda r: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(service, "read_rd_terminal_in_session", lambda *a: actual)
    return NS(t=t, root=root, stage=stage, session=session, added=added, actual=actual)


@pytest.mark.parametrize("status", ["running", "pause_requested", "cancel_requested"])
def test_positive_boundary_never_grants_feedback_release(boundary, status):
    b = boundary
    b.root.status = status
    row, facts = service.observe_rd_terminal(
        b.session, b.root.owner_id, b.root.id, b.stage.id, 1, b.t.settings
    )
    assert isinstance(row, ResearchWorkflowCheckpoint) and facts == b.actual
    assert (
        b.root.status
        == {
            "running": "blocked",
            "pause_requested": "paused",
            "cancel_requested": "cancelled",
        }[status]
    )
    assert b.root.revision == 2 and b.root.latest_checkpoint_id == row.id
    assert len(b.added) == 1


@pytest.mark.parametrize("change", ["cas", "stage", "missing_containment"])
def test_no_checkpoint_before_exact_boundary(boundary, monkeypatch, change):
    b = boundary
    if change == "cas":
        b.root.revision = 2
    elif change == "stage":
        b.stage.ordinal = 2
    else:
        monkeypatch.setattr(
            service,
            "read_rd_terminal_in_session",
            lambda *a: (_ for _ in ()).throw(
                DomainError("workflow_waiting_containment", "No stop", 409)
            ),
        )
    with pytest.raises(DomainError):
        service.observe_rd_terminal(
            b.session, b.root.owner_id, b.root.id, b.stage.id, 1, b.t.settings
        )
    assert not b.added
