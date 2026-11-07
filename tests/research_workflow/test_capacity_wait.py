"""Real bounded SQL selection and diagnostic writes on SQLite projections.

These tables deliberately omit PostgreSQL triggers/constraints. Claim and input
readers are explicit doubles; this tests scheduling/CAS, not engine admission.
"""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.app import research_lifecycle
from backend.app import research_workflow_admission as admission
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCommand,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.services import DomainError
from packages.research_workflow.admission import SelectWorkflowInputs
from workers.research import workflow_tick as tick

CAPACITY_WAIT = "workflow_input_capacity_exceeded"


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite://")
    tables = MetaData()
    for model in (
        ResearchWorkflow,
        ResearchWorkflowStage,
        ResearchWorkflowCommand,
        ResearchWorkflowRevision,
    ):
        Table(
            model.__tablename__,
            tables,
            *(
                Column(column.name, column.type, primary_key=column.primary_key)
                for column in model.__table__.columns
            ),
        )
    tables.create_all(engine)
    with engine.begin() as connection:
        for ddl in (
            "CREATE TABLE research_input_derivations (id TEXT, stage_id TEXT)",
            "CREATE TABLE research_input_derivation_events (id TEXT, derivation_id TEXT, state TEXT)",
            "CREATE TABLE research_runs (id TEXT, owner_id TEXT, status TEXT)",
        ):
            connection.exec_driver_sql(ddl)
    factory = sessionmaker(engine)
    monkeypatch.setattr(tick, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(tick, "require_schema", lambda session: None)
    monkeypatch.setattr(admission, "require_schema", lambda session: None)
    monkeypatch.setattr(admission, "resolve_model_input_versions", lambda *a: ())
    monkeypatch.setattr(admission, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(research_lifecycle, "_try_neutral_lock", lambda *a: None)
    owner = uuid4()
    count = 0

    def add(
        *,
        status="blocked",
        reason="workflow_next_child_pending",
        stage=True,
        kind="experiment_feedback_projection",
    ):
        nonlocal count
        workflow_id, stage_id = uuid4(), uuid4() if stage else None
        when = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=count)
        count += 1
        with factory.begin() as session:
            child_id = uuid4() if kind == "rd_factor_generation" else None
            if child_id is not None:
                session.execute(
                    text(
                        "INSERT INTO research_runs (id, owner_id, status) VALUES (:id, :owner, :status)"
                    ),
                    {"id": child_id.hex, "owner": owner.hex, "status": "succeeded"},
                )
            if stage:
                session.add(
                    ResearchWorkflowStage(
                        id=stage_id,
                        owner_id=owner,
                        workflow_id=workflow_id,
                        kind=kind,
                        child_run_id=child_id,
                    )
                )
            session.add(
                ResearchWorkflow(
                    id=workflow_id,
                    owner_id=owner,
                    revision=1,
                    status=status,
                    blocked_reason=reason,
                    current_stage_id=stage_id,
                    current_input_revision_id=uuid4(),
                    latest_checkpoint_id=uuid4() if stage else None,
                    created_at=when,
                    updated_at=when,
                )
            )
        return workflow_id

    yield NS(factory=factory, owner=owner, add=add, settings=NS(storage_root="unused"))
    engine.dispose()


@pytest.mark.parametrize(
    "kind",
    [
        None,
        "external_normalization",
        "experiment_feedback_projection",
        "rd_factor_generation",
    ],
)
@pytest.mark.parametrize("error_code", [CAPACITY_WAIT, "resolved_input_limit"])
def test_four_capacity_waits_release_tick_slots_and_selection_rearms_only_one(
    database, monkeypatch, kind, error_code
):
    c = database
    initial = kind in {None, "external_normalization"}
    structured = kind == "rd_factor_generation"
    pending = (
        "workflow_child_adapter_unavailable"
        if initial
        else "workflow_structured_pending"
        if structured
        else "workflow_next_child_pending"
    )
    wait_reason = (
        "workflow_initial_input_capacity_exceeded"
        if initial
        else "workflow_structured_input_capacity_exceeded"
        if structured
        else CAPACITY_WAIT
    )
    waits = [c.add(reason=pending, stage=kind is not None, kind=kind) for _ in range(4)]
    ready = c.add(status="ready", reason=None, stage=False)
    with c.factory() as session:
        frozen = {
            workflow_id: (
                session.get(ResearchWorkflow, workflow_id).current_stage_id,
                session.get(ResearchWorkflow, workflow_id).latest_checkpoint_id,
            )
            for workflow_id in waits
        }
    attempted, admitted = [], []
    available = set()

    def next_child(*args, **kwargs):
        workflow_id = kwargs["command"].workflow_id
        attempted.append(workflow_id)
        if workflow_id not in available:
            raise DomainError(error_code, "No room for feedback", 409)
        admitted.append(workflow_id)

    from backend.app import research_workflow_oe_claim

    monkeypatch.setattr(tick, "dispatch_next_rd_stage", next_child)
    monkeypatch.setattr(tick, "checkpoint_rd_stage", lambda *a, **kw: None)
    monkeypatch.setattr(research_workflow_oe_claim, "dispatch_oe_stage", next_child)
    monkeypatch.setattr(
        tick,
        "dispatch_initial_rd_stage",
        lambda *a, **kw: (
            next_child(*a, **kw)
            if kw["command"].workflow_id in waits
            else admitted.append(kw["command"].workflow_id)
        ),
    )
    first = tick.tick_workflows(c.factory, c.settings)
    assert [(r.workflow_id, r.state) for r in first] == [(w, "blocked") for w in waits]
    assert attempted == waits and not admitted
    with c.factory() as session:
        saved = list(
            session.scalars(
                select(ResearchWorkflow).where(ResearchWorkflow.id.in_(waits))
            )
        )
        assert all(r.blocked_reason == wait_reason and r.revision == 2 for r in saved)

    for _ in range(2):
        outcomes = tick.tick_workflows(c.factory, c.settings)
        assert [(r.workflow_id, r.state) for r in outcomes] == [(ready, "child_queued")]
    assert attempted == waits and admitted == [ready, ready]

    # A real validated input-selection write, not the tick, rearms continuation.
    command = SelectWorkflowInputs(
        workflow_id=waits[0], expected_revision=2, idempotency_key="make-room"
    )
    with c.factory.begin() as session:
        result = admission.select_workflow_inputs(session, c.owner, command, c.settings)
        row = session.get(ResearchWorkflow, waits[0])
        assert result.revision == 3 and row.status == "blocked"
        assert row.blocked_reason == pending
        assert (row.current_stage_id, row.latest_checkpoint_id) == frozen[waits[0]]
    available.add(waits[0])
    outcomes = tick.tick_workflows(c.factory, c.settings)
    assert [(r.workflow_id, r.state) for r in outcomes] == [
        (waits[0], "structured_child_queued" if structured else "child_queued"),
        (ready, "child_queued"),
    ]
    assert attempted == waits + waits[:1]
    with c.factory() as session:
        for workflow_id in waits[1:]:
            row = session.get(ResearchWorkflow, workflow_id)
            assert row.blocked_reason == wait_reason and row.revision == 2


@pytest.mark.parametrize(
    "status,selected",
    [
        ("ready", False),
        ("blocked", False),
        ("paused", False),
        ("pause_requested", True),
        ("cancel_requested", True),
    ],
)
@pytest.mark.parametrize(
    "reason",
    [
        CAPACITY_WAIT,
        "workflow_initial_input_capacity_exceeded",
        "workflow_structured_input_capacity_exceeded",
    ],
)
def test_capacity_wait_never_suppresses_pending_controls(
    database, monkeypatch, status, selected, reason
):
    c = database
    workflow_id = c.add(status=status, reason=reason)
    visited = []

    def observe(*args, **kwargs):
        visited.append(kwargs["workflow_id"])
        return tick.TickOutcome(kwargs["workflow_id"], "control_pending_or_terminal")

    monkeypatch.setattr(tick, "drive_one", observe)
    tick.tick_workflows(c.factory, c.settings)
    assert visited == ([workflow_id] if selected else [])
