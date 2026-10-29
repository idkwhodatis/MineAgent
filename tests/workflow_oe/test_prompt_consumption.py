"""Real consumption helper + ORM transaction, not PostgreSQL graph authority.

SQLite holds only the consumption projection table here (no PG triggers/FKs).
Typed config/request use real contracts; prerequisite persisted operation rows
are explicit doubles. No helper, validator or request builder is mocked.
"""

import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from backend.app.oe_workflow_prompt_guard import bind_workflow_consumption
from backend.app.research_workflow_models import (
    ResearchWorkflowModelConsumption as Consumption,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import CandidateDocument
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.openevolve_structured.provider_contracts import (
    OEChatRequest,
    RenderedPrompt,
)
from packages.openevolve_structured.provider_response import build_request
from packages.openevolve_structured.workflow_prompt import SYSTEM, _block
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from tests.openevolve.test_workflow_run import config as config_raw


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    columns = []
    for column in Consumption.__table__.columns:
        kind = "INTEGER" if column.name == "generation" else "TEXT"
        columns.append(
            column.name
            + " "
            + kind
            + (" PRIMARY KEY" if column.name == "id" else " NOT NULL")
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE research_workflow_model_consumption ("
                + ",".join(columns)
                + ", UNIQUE(stage_id, operation_id))"
            )
        )
    yield engine
    engine.dispose()


def arranged():
    config = WorkflowStructuredRunConfig.model_validate_json(
        canonical_bytes(config_raw())
    )
    parent = canonical_bytes(
        CandidateDocument(program=config.search.domain.seed_program)
    )
    rendered = RenderedPrompt(
        system=SYSTEM,
        user="Actual immutable seed: "
        + parent.decode()
        + _block(config.workflow_context),
        references=(),
        current_candidate_sha256=hashlib.sha256(parent).hexdigest(),
        artifact_section_sha256=None,
    )
    context = SearchOperationContext(
        owner_id=config.search.owner_id,
        run_id=config.search.run_id,
        epoch_id=uuid4(),
        iteration_intent_id=uuid4(),
        native_iteration=1,
        parent_candidate_id=uuid4(),
        parent_candidate_sha256=hashlib.sha256(parent).hexdigest(),
        execution_binding_sha256=config.search.execution_binding_sha256,
        selection_snapshot_sha256="a" * 64,
        rendered_context_sha256=digest(rendered),
    )
    request = build_request(
        binding=config.model_binding,
        context=context,
        domain=config.search.domain,
        parent_candidate=parent,
        rendered=rendered,
    )
    fence = NS(
        owner_id=config.search.owner_id,
        run_id=config.search.run_id,
        attempt_id=uuid4(),
        generation=1,
    )
    scope = vars(fence)
    operation = NS(
        **scope,
        id=uuid4(),
        request_sha256=request.sha256,
        request=request.model_dump(mode="json"),
    )
    transport = NS(
        **scope, id=uuid4(), operation_id=operation.id, ordinal=1, state="submitted"
    )
    member = NS(
        **scope,
        id=uuid4(),
        operation_id=operation.id,
        transport_id=transport.id,
        request_sha256=request.sha256,
    )
    return NS(
        config=config,
        request=request,
        fence=fence,
        operation=operation,
        transport=transport,
        member=member,
    )


def bind(session, value):
    return bind_workflow_consumption(
        session,
        value.fence,
        value.config,
        value.member,
        value.operation,
        value.transport,
        value.request,
    )


def count(session):
    return session.scalar(select(func.count()).select_from(Consumption))


def test_exact_transport_consumption_and_immutable_replay_same_transaction(engine):
    value = arranged()
    with Session(engine) as session, session.begin():
        tx = session.get_transaction()
        row = bind(session, value)
        assert session.get_transaction() is tx
        assert row.input_sha256 == value.config.workflow_context.inputs.sha256
        assert row.input_sha256 != value.config.workflow_context.inputs.selection.sha256
        assert (row.stage_id, row.input_revision_id, row.workflow_id) == (
            value.config.workflow_context.inputs.binding.stage_id,
            value.config.workflow_context.inputs.binding.input_revision_id,
            value.config.workflow_context.inputs.binding.workflow_id,
        )
        assert row.transport_id == value.transport.id
        assert row.request_sha256 == value.request.sha256
        saved_id = row.id
        assert bind(session, value) is row
        assert count(session) == 1
    with Session(engine) as session, session.begin():
        assert bind(session, value).id == saved_id
        assert count(session) == 1


def test_outer_rollback_removes_flushed_consumption(engine):
    value = arranged()
    with pytest.raises(RuntimeError), Session(engine) as session, session.begin():
        bind(session, value)
        assert count(session) == 1
        raise RuntimeError("abort entire admission transaction")
    with Session(engine) as session:
        assert count(session) == 0


@pytest.mark.parametrize("target", ["member", "operation", "transport"])
@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_each_prerequisite_scope_must_match_exact_fence(engine, target, field):
    value = arranged()
    setattr(getattr(value, target), field, 2 if field == "generation" else uuid4())
    with Session(engine) as session, session.begin():
        with pytest.raises(ValueError):
            bind(session, value)
        assert count(session) == 0


@pytest.mark.parametrize(
    "target,field,bad",
    [
        ("operation", "request_sha256", "f" * 64),
        ("member", "request_sha256", "f" * 64),
        ("member", "operation_id", None),
        ("member", "transport_id", None),
        ("transport", "operation_id", None),
        ("transport", "ordinal", 2),
        ("transport", "state", "prepared"),
        ("operation", "request", {}),
    ],
)
def test_transport_or_request_binding_drift_has_no_consumption(
    engine, target, field, bad
):
    value = arranged()
    setattr(getattr(value, target), field, bad)
    with Session(engine) as session, session.begin():
        with pytest.raises(ValueError):
            bind(session, value)
        assert count(session) == 0


@pytest.mark.parametrize("change", ["missing", "duplicate", "system", "context"])
def test_consistently_rehashed_request_still_needs_exact_rendered_evidence(
    engine, change
):
    value = arranged()
    raw = value.request.model_dump(mode="json")
    if change == "missing":
        raw["messages"][1]["content"] = "evidence omitted"
    elif change == "duplicate":
        raw["messages"][1]["content"] += _block(value.config.workflow_context)
    elif change == "system":
        raw["messages"][0]["content"] = "Unauthorized replacement"
    else:
        raw["messages"][1]["content"] = raw["messages"][1]["content"].replace(
            str(value.config.workflow_context.rd_checkpoint_id), str(uuid4())
        )
    value.request = OEChatRequest.model_validate_json(canonical_bytes(raw))
    value.operation.request = value.request.model_dump(mode="json")
    value.operation.request_sha256 = value.member.request_sha256 = value.request.sha256
    with Session(engine) as session, session.begin():
        with pytest.raises(ValueError):
            bind(session, value)
        assert count(session) == 0


@pytest.mark.parametrize(
    "field",
    [
        "input_sha256",
        "input_revision_id",
        "transport_id",
        "request_sha256",
        "attempt_id",
        "generation",
    ],
)
def test_existing_consumption_cannot_be_rebound(engine, field):
    value = arranged()
    with Session(engine) as session, session.begin():
        row = bind(session, value)
        setattr(
            row,
            field,
            2
            if field == "generation"
            else ("f" * 64 if field.endswith("sha256") else uuid4()),
        )
        session.flush()
        with pytest.raises(ValueError, match="replay differs"):
            bind(session, value)
        assert count(session) == 1


@pytest.mark.parametrize("field", ["owner_id", "run_id"])
def test_fence_cannot_adopt_another_workflow_child(engine, field):
    value = arranged()
    setattr(value.fence, field, uuid4())
    # Keep the actual operation scope internally consistent with the wrong fence:
    # the immutable config must independently reject that substituted child.
    for row in (value.member, value.operation, value.transport):
        setattr(row, field, getattr(value.fence, field))
    with Session(engine) as session, session.begin():
        with pytest.raises(ValueError):
            bind(session, value)
        assert count(session) == 0


def test_untyped_config_never_creates_consumption(engine):
    value = arranged()
    value.config = NS(**value.config.model_dump(mode="python"))
    with Session(engine) as session, session.begin():
        with pytest.raises(ValueError, match="Exact workflow OE config"):
            bind(session, value)
        assert count(session) == 0
