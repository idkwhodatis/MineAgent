"""Real bounded selection and diagnostic writes over SQLite projections.

Physical checkpoints and attachment verification are explicit doubles. This
exercises cross-module scheduling, not PostgreSQL constraints or engine safety.
"""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from backend.app import (
    oe_structured_identity,
    oe_workflow_metadata,
    research_lifecycle,
    research_workflow_oe_checkpoint,
)
from backend.app.models import ResearchRun
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.services import DomainError
from workers.research import workflow_tick as tick


@pytest.mark.parametrize("child_status", ["succeeded", "failed", "cancelled"])
def test_four_structured_waits_cannot_starve_new_workflow(monkeypatch, child_status):
    engine = create_engine("sqlite://")
    tables = MetaData()
    for model in (ResearchWorkflow, ResearchWorkflowStage, ResearchRun):
        Table(
            model.__tablename__,
            tables,
            *(
                Column(
                    column.name,
                    JSON if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                )
                for column in model.__table__.columns
            ),
        )
    tables.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE research_input_derivations (id TEXT, stage_id TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE research_input_derivation_events "
            "(id TEXT, derivation_id TEXT, state TEXT)"
        )
    factory = sessionmaker(engine)
    owner = uuid4()
    waits = [uuid4() for _ in range(4)]
    ready = uuid4()
    receipts = {}
    with factory.begin() as session:
        for index, workflow_id in enumerate([*waits, ready]):
            waiting = index < 4
            stage_id, run_id = (uuid4(), uuid4()) if waiting else (None, None)
            when = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)
            if waiting:
                session.add(
                    ResearchWorkflowStage(
                        id=stage_id,
                        owner_id=owner,
                        workflow_id=workflow_id,
                        kind="structured_factor_execution",
                        child_run_id=run_id,
                    )
                )
                session.add(
                    ResearchRun(
                        id=run_id,
                        owner_id=owner,
                        purpose="strategy_search",
                        engine="openevolve",
                        status=child_status,
                    )
                )
                receipts[run_id] = NS(
                    owner_id=owner,
                    workflow_id=workflow_id,
                    stage_id=stage_id,
                    run_id=run_id,
                )
            session.add(
                ResearchWorkflow(
                    id=workflow_id,
                    owner_id=owner,
                    revision=1,
                    status="running" if waiting else "ready",
                    current_stage_id=stage_id,
                    current_input_revision_id=uuid4(),
                    created_at=when,
                    updated_at=when,
                )
            )
    monkeypatch.setattr(tick, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(tick, "require_schema", lambda *a: None)
    monkeypatch.setattr(research_lifecycle, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(oe_structured_identity, "decode_spec", lambda *a: (None, None))
    monkeypatch.setattr(
        oe_workflow_metadata,
        "require_attachment",
        lambda session, run, *a: receipts[run.id],
    )
    waiting_containment = child_status == "succeeded"
    reason = (
        "workflow_waiting_containment"
        if waiting_containment
        else f"workflow_child_{child_status}_unverified"
    )

    def pending(*args, **kwargs):
        raise DomainError(
            "workflow_waiting_containment"
            if waiting_containment
            else "workflow_waiting_child",
            "No verified terminal checkpoint",
            409,
        )

    monkeypatch.setattr(research_workflow_oe_checkpoint, "checkpoint_oe_stage", pending)
    admitted = []
    monkeypatch.setattr(
        tick,
        "dispatch_initial_rd_stage",
        lambda *a, **kw: admitted.append(kw["command"].workflow_id),
    )
    settings = NS(storage_root="unused")
    try:
        first = tick.tick_workflows(factory, settings)
        assert [(r.workflow_id, r.state) for r in first] == [(w, reason) for w in waits]
        assert not admitted
        for _ in range(2):
            outcomes = tick.tick_workflows(factory, settings)
            assert outcomes[0] == tick.TickOutcome(ready, "child_queued")
            assert [r.workflow_id for r in outcomes[1:]] == waits[:3]
        assert admitted == [ready, ready]
        with factory() as session:
            for workflow_id in waits:
                root = session.get(ResearchWorkflow, workflow_id)
                assert root.blocked_reason == reason and root.revision == 2
                assert root.status == ("running" if waiting_containment else "blocked")
                assert root.latest_checkpoint_id is None
            assert set(session.scalars(select(ResearchRun.status))) == {child_status}
    finally:
        engine.dispose()
