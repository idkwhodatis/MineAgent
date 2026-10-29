"""Portable origin projection guards; not a public HTTP/runtime acceptance claim."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import engine_input_routes as routes
from backend.app.services import DomainError


@pytest.fixture
def source_graph(monkeypatch):
    monkeypatch.setattr(
        routes,
        "rd_config",
        lambda run: NS(schema_version="mineagent.factor-research.v1"),
    )
    from backend.app.factor_models import FactorVersion
    from backend.app.rdagent_models import (
        FactorGenerationModelOperation,
        FactorResponseAssemblyPart,
    )
    from backend.app.research_broker_models import (
        ResearchModelOperation,
        ResearchModelOutcome,
        ResearchModelTransport,
    )

    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }

    def row(**kwargs):
        return NS(**scope, **kwargs)

    run = NS(id=scope["run_id"], owner_id=scope["owner_id"])
    source = row(
        id=uuid4(),
        start_id=uuid4(),
        factor_version_id=uuid4(),
        source_sha256="a" * 64,
        assembly_id=uuid4(),
        call_id=uuid4(),
        context_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    version = NS(
        id=source.factor_version_id,
        owner_id=run.owner_id,
        factor_id=uuid4(),
        code_sha256=source.source_sha256,
    )
    witness = row(
        id=uuid4(),
        assembly_id=source.assembly_id,
        call_id=source.call_id,
        source_sha256=source.source_sha256,
        part_count=1,
    )
    part = row(
        id=uuid4(),
        assembly_part_id=uuid4(),
        member_id=uuid4(),
        outcome_id=uuid4(),
        response_object_id=uuid4(),
        response_sha256="b" * 64,
        response_object_kind="model_response",
        ordinal=0,
    )
    member = row(
        id=part.member_id,
        operation_id=uuid4(),
        start_id=source.start_id,
        protocol="mineagent.rdagent-chat.v1",
        call_id=source.call_id,
        context_id=source.context_id,
    )
    operation = row(
        id=member.operation_id,
        protocol="mineagent.rdagent-chat.v1",
        context_id=source.context_id,
        request_sha256="c" * 64,
    )
    transport = row(id=uuid4(), operation_id=operation.id, state="completed")
    outcome = row(
        id=part.outcome_id,
        transport_id=transport.id,
        outcome="completed",
        response_object_id=part.response_object_id,
        response_sha256=part.response_sha256,
        response_object_kind="model_response",
    )
    assembly = row(
        id=part.assembly_part_id,
        assembly_id=source.assembly_id,
        call_id=source.call_id,
        ordinal=part.ordinal,
        response_object_id=part.response_object_id,
        response_object_kind=part.response_object_kind,
        member_id=part.member_id,
        outcome_id=part.outcome_id,
        response_sha256=part.response_sha256,
    )
    values = {
        FactorVersion: version,
        FactorGenerationModelOperation: member,
        ResearchModelOperation: operation,
        ResearchModelTransport: transport,
        ResearchModelOutcome: outcome,
        FactorResponseAssemblyPart: assembly,
    }
    session = NS(
        get=lambda model, key: values[model],
        scalar=lambda _: witness,
        scalars=lambda _: NS(all=lambda: [part]),
    )
    return NS(
        run=run,
        source=source,
        session=session,
        witness=witness,
        member=member,
        part=part,
        outcome=outcome,
        assembly=assembly,
        operation=operation,
        transport=transport,
    )


def test_exact_response_witness_is_agent_origin_not_approval(source_graph):
    g = source_graph
    result = routes.source_item(g.session, g.run, g.source, witnesses_available=True)
    assert result.origin == "agent_generated" and not result.default_pinned
    assert len(result.operations) == 1 and result.submission is None
    assert "approved" not in result.model_dump()
    assert (
        "request" not in result.model_dump() and "response" not in result.model_dump()
    )


@pytest.mark.parametrize("missing_witness", [False, True])
def test_owner_and_source_rows_alone_never_establish_origin(
    source_graph, missing_witness
):
    g = source_graph
    if missing_witness:
        g.session.scalar = lambda _: None
    result = routes.source_item(
        g.session, g.run, g.source, witnesses_available=missing_witness
    )
    assert (
        result.origin == "unknown"
        and not result.default_pinned
        and result.operations == ()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "scope",
        "response",
        "assembly",
        "call",
        "ordinal",
        "object",
        "kind",
        "start",
        "protocol",
        "context",
        "transport",
    ],
)
def test_drift_is_not_downgraded_to_a_successful_origin(source_graph, mutation):
    g = source_graph
    if mutation == "owner":
        g.source.owner_id = uuid4()
    elif mutation == "scope":
        g.member.attempt_id = uuid4()
    elif mutation == "response":
        g.outcome.response_sha256 = "d" * 64
    elif mutation == "assembly":
        g.assembly.assembly_id = uuid4()
    elif mutation == "call":
        g.assembly.call_id = uuid4()
    elif mutation == "ordinal":
        g.assembly.ordinal = 1
    elif mutation == "object":
        g.assembly.response_object_id = uuid4()
    elif mutation == "kind":
        g.assembly.response_object_kind = "code"
    elif mutation == "start":
        g.member.start_id = uuid4()
    elif mutation == "protocol":
        g.member.protocol = "mineagent.rdagent-embedding.v1"
    elif mutation == "context":
        g.operation.context_id = uuid4()
    else:
        g.transport.state = "unknown"
    with pytest.raises(DomainError) as caught:
        routes.source_item(g.session, g.run, g.source, witnesses_available=True)
    assert caught.value.code == "input_provenance_inconsistent"


def test_saved_submission_requires_immutable_binding_before_user_label(monkeypatch):
    monkeypatch.setattr(
        routes,
        "rd_config",
        lambda run: NS(schema_version="mineagent.factor-research.v1"),
    )
    calls = []
    row = NS(
        id=uuid4(),
        plan_id=uuid4(),
        idempotency_key="private-key",
        request_sha256="e" * 64,
        created_at=datetime.now(UTC),
    )
    run = NS(owner_id=uuid4())

    def validate(*args):
        calls.append("validated")

    def parse(*args):
        assert calls == ["validated"]

    monkeypatch.setattr(routes, "validate_saved_binding", validate)
    monkeypatch.setattr(routes, "read_request", parse)
    result = routes.submission_item(NS(get=lambda *args: object()), run, row)
    assert result.origin == "user_provided" and result.default_pinned
    assert "idempotency_key" not in result.model_dump()

    def drift(*args):
        raise DomainError("submission_receipt_drift", "drift", 409)

    monkeypatch.setattr(routes, "validate_saved_binding", drift)
    with pytest.raises(DomainError):
        routes.submission_item(NS(get=lambda *args: object()), run, row)


def test_wrong_owner_is_checked_before_reading_any_input(monkeypatch):
    def deny(*args):
        raise DomainError("not_found", "Not found", 404)

    monkeypatch.setattr(routes, "owned_engine_run", deny)
    with pytest.raises(DomainError) as caught:
        routes.read_inputs(uuid4(), NS(id=uuid4()), object(), 0)
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "schema,protocol,valid",
    [
        ("mineagent.factor-research.v2", "mineagent.rdagent-provider-chat.v1", True),
        ("mineagent.factor-research.v2", "mineagent.rdagent-chat.v1", False),
        ("mineagent.factor-research.v1", "mineagent.rdagent-provider-chat.v1", False),
        (
            "mineagent.factor-research.v2",
            "mineagent.rdagent-provider-embedding.v1",
            False,
        ),
    ],
)
def test_generated_origin_requires_exact_configured_chat_family(
    source_graph, monkeypatch, schema, protocol, valid
):
    g = source_graph
    monkeypatch.setattr(routes, "rd_config", lambda run: NS(schema_version=schema))
    g.operation.protocol = g.member.protocol = protocol
    if valid:
        result = routes.source_item(
            g.session, g.run, g.source, witnesses_available=True
        )
        assert result.origin == "agent_generated" and not result.default_pinned
    else:
        with pytest.raises(DomainError, match="provenance differs"):
            routes.source_item(g.session, g.run, g.source, witnesses_available=True)


def test_conflicting_fresh_and_seeded_receipts_never_establish_user_origin(monkeypatch):
    run = NS(id=uuid4(), owner_id=uuid4())
    monkeypatch.setattr(routes, "owned_engine_run", lambda *a: run)
    session = NS(
        scalars=lambda _: NS(all=lambda: ["026_rd_seeded_submissions"]),
        scalar=lambda _: object(),
    )
    with pytest.raises(DomainError) as caught:
        routes.read_inputs(run.id, NS(id=run.owner_id), session, 0)
    assert caught.value.code == "input_provenance_inconsistent"


@pytest.mark.parametrize(
    "source_schema,expected",
    [
        ("mineagent.rd-source-restore-closure.v7", "agent_generated"),
        ("mineagent.rd-source-restore-closure.v8", "unknown"),
        ("mineagent.rd-source-restore-closure.unknown", "unknown"),
    ],
)
def test_seeded_head_preserves_exact_source_witness_only(
    source_graph, monkeypatch, source_schema, expected
):
    from dataclasses import replace

    g = source_graph
    g.run.created_at = g.source.created_at
    monkeypatch.setattr(routes, "owned_engine_run", lambda *args: g.run)
    monkeypatch.setattr(
        routes,
        "rd_config",
        lambda run: NS(schema_version="mineagent.factor-research.v1", sha256="f" * 64),
    )
    capabilities = routes.capabilities_for("027c_seeded_controller_v4")
    assert (
        capabilities.rd_source_closure_schema
        == "mineagent.rd-source-restore-closure.v7"
    )
    monkeypatch.setattr(
        routes,
        "capabilities_for",
        lambda revision: replace(capabilities, rd_source_closure_schema=source_schema),
    )
    scalar_rows = iter([None, None, g.witness])
    rows = iter([["027c_seeded_controller_v4"], [g.source], [g.part]])
    g.session.scalar = lambda statement: next(scalar_rows)
    g.session.scalars = lambda statement: NS(all=lambda: next(rows))
    result = routes.read_inputs(g.run.id, NS(id=g.run.owner_id), g.session, 0)
    item = next(item for item in result.items if item.id == g.source.id)
    assert item.origin == expected
    assert not item.default_pinned
    assert len(item.operations) == (1 if expected == "agent_generated" else 0)


def test_unknown_input_history_revision_is_rejected(source_graph, monkeypatch):
    g = source_graph
    monkeypatch.setattr(routes, "owned_engine_run", lambda *args: g.run)
    g.session.scalars = lambda statement: NS(all=lambda: ["027d_unknown"])
    with pytest.raises(DomainError) as caught:
        routes.read_inputs(g.run.id, NS(id=g.run.owner_id), g.session, 0)
    assert caught.value.code == "input_history_schema_unavailable"
