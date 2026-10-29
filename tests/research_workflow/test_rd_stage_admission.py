"""Fresh stage graph doubles; no claim of database or engine execution."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_rd_stage as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.rd_stage import RDStageDefinition
from tests.research_workflow.test_plan import plan


class Session:
    def __init__(self, rows, stages):
        self.rows = iter(rows)
        self.stages = stages

    def scalar(self, query):
        return next(self.rows)

    def scalars(self, query):
        return self.stages


@pytest.fixture
def graph(monkeypatch):
    p = plan()
    w, s, r = uuid4(), uuid4(), uuid4()
    selection = WorkflowInputSnapshot(owner_id=p.owner_id, workflow_id=w, revision_id=r)
    definition = RDStageDefinition(
        plan_sha256=p.sha256,
        research_iteration=0,
        predecessor_checkpoint_id=None,
        parent_factor_version_id=p.initial_parent_factor_version_id,
        parent_source_stage_id=None,
    )
    root = NS(
        owner_id=p.owner_id,
        id=w,
        current_stage_id=s,
        current_input_revision_id=r,
        status="running",
        latest_checkpoint_id=None,
        mode=p.mode,
        plan_canonical=canonical_bytes(p).decode(),
        plan_sha256=p.sha256,
        budget_canonical=canonical_bytes(p.budget_ceiling).decode(),
        budget_sha256=digest(p.budget_ceiling),
    )
    stage = NS(
        owner_id=p.owner_id,
        workflow_id=w,
        id=s,
        ordinal=1,
        kind="rd_factor_generation",
        child_run_id=uuid4(),
        input_revision_id=r,
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=definition.sha256,
        allocation_canonical=canonical_bytes(p.research_template.budget).decode(),
        allocation_sha256=digest(p.research_template.budget),
    )
    revision = NS(
        owner_id=p.owner_id,
        workflow_id=w,
        id=r,
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    calls = []
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        service, "require_publication_admission", lambda *a: calls.append(a[-1])
    )
    return p, root, stage, revision, calls


def invoke(graph):
    p, root, stage, revision, _calls = graph
    return service.require_current_rd_stage(
        Session([root, revision], [stage]),
        p.owner_id,
        root.id,
        stage.id,
        object(),
        admission="test-boundary-sentinel",
    )


def test_fresh_initial_stage_returns_actual_graph(graph):
    value = invoke(graph)
    assert value.root is graph[1] and value.stage is graph[2]
    assert value.selection.revision_id == graph[3].id
    assert graph[4] == ["test-boundary-sentinel"] * 2


@pytest.mark.parametrize(
    "target,field,value",
    [
        (1, "status", "cancel_requested"),
        (1, "current_input_revision_id", uuid4()),
        (2, "child_run_id", None),
        (2, "ordinal", 2),
        (2, "owner_id", uuid4()),
        (2, "allocation_sha256", "f" * 64),
        (3, "inputs_sha256", "f" * 64),
    ],
)
def test_changed_current_graph_rejected(graph, target, field, value):
    setattr(graph[target], field, value)
    with pytest.raises(DomainError):
        invoke(graph)


def test_later_iteration_not_fake_authorized(graph):
    p, _root, stage, _revision, _calls = graph
    definition = RDStageDefinition(
        plan_sha256=p.sha256,
        research_iteration=1,
        predecessor_checkpoint_id=uuid4(),
        parent_factor_version_id=uuid4(),
        parent_source_stage_id=uuid4(),
    )
    stage.definition_canonical = canonical_bytes(definition).decode()
    stage.definition_sha256 = definition.sha256
    with pytest.raises(DomainError):
        invoke(graph)


def test_normalized_predecessor_requires_actual_checkpoint_replay(graph, monkeypatch):
    from packages.research_runtime.budget_contracts import BudgetVector

    p, root, stage, revision, _calls = graph
    p = p.model_copy(
        update={
            "normalize_external_first": True,
            "budget_ceiling": p.budget_ceiling.add(BudgetVector(storage_bytes=294912)),
        }
    )
    root.plan_canonical = canonical_bytes(p).decode()
    root.plan_sha256 = p.sha256
    root.budget_canonical = canonical_bytes(p.budget_ceiling).decode()
    root.budget_sha256 = digest(p.budget_ceiling)
    previous = NS(
        owner_id=p.owner_id,
        workflow_id=root.id,
        ordinal=1,
        kind="external_normalization",
        id=uuid4(),
    )
    checkpoint = NS(id=uuid4())
    root.latest_checkpoint_id = checkpoint.id
    definition = RDStageDefinition(
        plan_sha256=p.sha256,
        research_iteration=0,
        predecessor_checkpoint_id=checkpoint.id,
        parent_factor_version_id=p.initial_parent_factor_version_id,
        parent_source_stage_id=None,
    )
    stage.ordinal = 2
    stage.definition_canonical = canonical_bytes(definition).decode()
    stage.definition_sha256 = definition.sha256
    root.revision = 4
    seen = []

    def observe(*args, **kwargs):
        seen.append((args[3], kwargs["admission"]))
        return checkpoint.id, NS(state="succeeded")

    monkeypatch.setattr(service, "observe_external_terminal", observe)
    result = service.require_current_rd_stage(
        Session([root, revision, checkpoint], [previous, stage]),
        p.owner_id,
        root.id,
        stage.id,
        object(),
        admission="test-boundary-sentinel",
    )
    assert result.stage is stage
    assert seen == [(previous.id, "test-boundary-sentinel")]


@pytest.mark.parametrize("change", [None, "missing_stop", "checkpoint"])
def test_later_current_stage_requires_full_predecessor_not_owner_status(
    graph, monkeypatch, change
):
    from contextlib import contextmanager

    from backend.app import (
        research_workflow_native_guard,
        research_workflow_rd_predecessor,
    )

    p, root, stage, revision, _calls = graph
    checkpoint_id, parent_id = uuid4(), uuid4()
    definition = RDStageDefinition(
        plan_sha256=p.sha256,
        research_iteration=1,
        predecessor_checkpoint_id=checkpoint_id,
        parent_factor_version_id=uuid4(),
        parent_source_stage_id=parent_id,
    )
    stage.ordinal = 3
    stage.definition_canonical = canonical_bytes(definition).decode()
    stage.definition_sha256 = definition.sha256
    root.latest_checkpoint_id = checkpoint_id
    previous = [
        NS(
            owner_id=p.owner_id,
            workflow_id=root.id,
            ordinal=1,
            kind="rd_factor_generation",
        ),
        NS(
            owner_id=p.owner_id,
            workflow_id=root.id,
            ordinal=2,
            kind="experiment_feedback_projection",
        ),
    ]
    events = []

    @contextmanager
    def borrowed(*a, **kw):
        events.append("actual_publication_guard_boundary")
        yield

    def verify(*a):
        events.append("full_predecessor")
        if change == "missing_stop":
            raise DomainError("workflow_waiting_containment", "No positive stop", 409)
        return NS(
            definition=definition,
            feedback_checkpoint_id=uuid4() if change == "checkpoint" else checkpoint_id,
        )

    monkeypatch.setattr(
        research_workflow_native_guard, "native_guard_from_publication", borrowed
    )
    monkeypatch.setattr(
        research_workflow_rd_predecessor, "verify_rd_predecessor", verify
    )

    def invoke_later():
        return service.require_current_rd_stage(
            Session([root, revision], [*previous, stage]),
            p.owner_id,
            root.id,
            stage.id,
            object(),
            admission=object(),
        )

    if change:
        with pytest.raises(DomainError):
            invoke_later()
    else:
        assert invoke_later().definition == definition
    assert events == ["actual_publication_guard_boundary", "full_predecessor"]
