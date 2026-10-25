"""Real PostgreSQL ledger races and physical fixture submission accounting.

These tests do not claim process containment; that is a separate required Docker
suite. No SQLite/session mocks or private API admission are used.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.research_broker_models import (
    ResearchBudgetControl,
    ResearchBudgetReservation,
    ResearchModelTransport,
)
from backend.app.research_broker_services import (
    initialize_budget,
    mark_submitted,
    open_context,
    open_operation,
    replay_completed,
    reserve_budget,
    reserve_transport,
    settle_budget,
    settle_transport,
)
from backend.app.research_lifecycle import neutral_transaction
from packages.research_runtime.broker_contracts import (
    ChatRequest,
    FixtureContext,
    Message,
    digest,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.control import BrokerAbort
from packages.research_runtime.fences import AttemptFence
from workers.research.fixture_transport import submit_fixture
from workers.research.model_broker import controlled_retry_sleep, publish_model_object

pytestmark = pytest.mark.postgres


@pytest.fixture
def broker_fixture(migrated_engine, owner, claimed_attempt, settings):
    with Session(migrated_engine) as session:
        attempt = session.get(ExecutionAttempt, claimed_attempt.id)
        attempt.lease_expires_at = utcnow() + timedelta(minutes=10)
        session.commit()
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    fence = AttemptFence(
        owner.user.id,
        claimed_attempt.run_id,
        claimed_attempt.id,
        claimed_attempt.generation,
        claimed_attempt.lease_token,
    )
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    (settings.storage_root / "research-objects").mkdir(exist_ok=True)
    with neutral_transaction(factory, fence) as session:
        initialize_budget(
            session,
            fence,
            BudgetVector(
                calls=10,
                tokens=10000,
                storage_bytes=16 * 1024 * 1024,
                process_slots=10,
                wall_milliseconds=600000,
                mutation_slots=10,
                evaluator_executions=10,
            ),
        )
    return factory, fence, settings


def seed_operation(factory, fence, settings, scenario="chat"):
    request = ChatRequest(
        messages=(Message(role="user", content="fixture input"),), max_output_tokens=16
    )
    ref = publish_model_object(factory, fence, "model_request", request, settings)
    with neutral_transaction(factory, fence) as session:
        context = open_context(
            session, fence, FixtureContext(scenario=scenario, ordinal=0)
        )
        operation = open_operation(
            session,
            fence,
            context.id,
            purpose="chat",
            ordinal=0,
            request=request,
            request_object=ref,
        )
        return operation.id, context.id, request, ref


def submit_once(factory, fence, settings, operation_id, request, scenario):
    with neutral_transaction(factory, fence) as session:
        ticket = reserve_transport(session, fence, operation_id, digest(request))
        ticket_id, ordinal = ticket.id, ticket.ordinal
    with neutral_transaction(factory, fence) as session:
        mark_submitted(session, fence, ticket_id)
    observed = submit_fixture(
        FixtureContext(scenario=scenario, ordinal=0), request, ordinal
    )
    response = (
        None
        if observed.response is None
        else publish_model_object(
            factory, fence, "model_response", observed.response, settings
        )
    )
    with neutral_transaction(factory, fence) as session:
        settle_transport(
            session,
            fence,
            ticket_id,
            outcome="failed" if response is None else "completed",
            usage=observed.usage,
            response_object=response,
            response=observed.response,
        )
    return response


def test_neutral_identical_physical_failures_charge_separately_and_completion_replays(
    broker_fixture,
):
    factory, fence, settings = broker_fixture
    operation, _, request, _ = seed_operation(factory, fence, settings, "retry")
    assert submit_once(factory, fence, settings, operation, request, "retry") is None
    assert submit_once(factory, fence, settings, operation, request, "retry") is None
    response = submit_once(factory, fence, settings, operation, request, "retry")
    with neutral_transaction(factory, fence) as session:
        assert replay_completed(session, fence, operation, digest(request)) == response
        assert replay_completed(session, fence, operation, digest(request)) == response
        control = session.scalar(select(ResearchBudgetControl))
        assert control.used["calls"] == 3
        assert control.reserved["calls"] == 0
        assert len(session.scalars(select(ResearchModelTransport)).all()) == 3
        with pytest.raises(BrokerAbort, match="uncertain_transport"):
            reserve_transport(session, fence, operation, digest(request))


def test_neutral_request_drift_cannot_create_another_logical_operation(broker_fixture):
    factory, fence, settings = broker_fixture
    operation, context, request, ref = seed_operation(factory, fence, settings)
    different = request.model_copy(update={"max_output_tokens": 17})
    with neutral_transaction(factory, fence) as session:
        with pytest.raises(BrokerAbort, match="request_drift"):
            open_operation(
                session,
                fence,
                context,
                purpose="chat",
                ordinal=0,
                request=different,
                request_object=ref,
            )
        assert (
            reserve_transport(session, fence, operation, digest(request)).ordinal == 1
        )


def test_neutral_lost_submission_remains_reserved_and_cannot_retry(broker_fixture):
    factory, fence, settings = broker_fixture
    operation, _, request, _ = seed_operation(factory, fence, settings)
    with neutral_transaction(factory, fence) as session:
        ticket = reserve_transport(session, fence, operation, digest(request))
        ticket_id = ticket.id
    with neutral_transaction(factory, fence) as session:
        mark_submitted(session, fence, ticket_id)
    # Simulate process loss after the durable pre-call boundary; no fake outcome.
    with neutral_transaction(factory, fence) as session:
        with pytest.raises(BrokerAbort, match="uncertain_transport"):
            reserve_transport(session, fence, operation, digest(request))
        row = session.get(ResearchModelTransport, ticket_id)
        held = session.get(ResearchBudgetReservation, row.reservation_id)
        assert held.state == "uncertain"
        with pytest.raises(BrokerAbort, match="invalid_accounting"):
            settle_budget(
                session, fence, held.id, charge=BudgetVector(), outcome="not_submitted"
            )
        assert session.scalar(select(ResearchBudgetControl)).reserved["calls"] == 1


def test_neutral_concurrent_reservations_cannot_overspend_run_cap(broker_fixture):
    factory, fence, _ = broker_fixture
    # Existing caps are immutable. Reserve the first nine calls before racing.
    with neutral_transaction(factory, fence) as session:
        reserve_budget(
            session,
            fence,
            purpose="model_transport",
            logical_key="already-held",
            amounts=BudgetVector(calls=9),
        )

    def reserve(index):
        deadline = time.monotonic() + 10
        while True:
            try:
                with neutral_transaction(factory, fence) as session:
                    reserve_budget(
                        session,
                        fence,
                        purpose="model_transport",
                        logical_key=f"race:{index}",
                        amounts=BudgetVector(calls=1),
                    )
                return "reserved"
            except BrokerAbort as exc:
                return exc.code
            except Exception as exc:
                if (
                    getattr(exc, "code", None) != "neutral_busy"
                    or time.monotonic() > deadline
                ):
                    raise
                time.sleep(0.01)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [1, 2]))
    assert sorted(results) == ["budget_exhausted", "reserved"]
    with neutral_transaction(factory, fence) as session:
        assert session.scalar(select(ResearchBudgetControl)).reserved["calls"] == 10


def test_neutral_budget_survives_generation_change_without_reset(broker_fixture):
    factory, fence, _ = broker_fixture
    with neutral_transaction(factory, fence) as session:
        prior = reserve_budget(
            session,
            fence,
            purpose="model_transport",
            logical_key="prior-generation",
            amounts=BudgetVector(calls=3),
        )
        settle_budget(
            session,
            fence,
            prior.id,
            charge=BudgetVector(calls=3),
            outcome="conservative_unknown",
        )
        caps = BudgetVector.model_validate(
            session.scalar(select(ResearchBudgetControl)).limits
        )
    with factory() as session:
        old = session.get(ExecutionAttempt, fence.attempt_id)
        old.status, old.finished_at = "failed", utcnow()
        run = session.get(ResearchRun, fence.run_id)
        new = ExecutionAttempt(
            id=uuid4(),
            run_id=run.id,
            generation=2,
            worker_id="neutral-fixture",
            lease_token=uuid4(),
            lease_expires_at=utcnow() + timedelta(minutes=10),
            status="running",
            sandbox_id="neutral-fixture-no-launch",
        )
        session.add(new)
        session.flush()
        run.generation, run.active_attempt_id, run.status = 2, new.id, "running"
        session.commit()
        next_fence = AttemptFence(
            fence.owner_id, fence.run_id, new.id, 2, new.lease_token
        )
    with neutral_transaction(factory, next_fence) as session:
        control = initialize_budget(session, next_fence, caps)
        assert control.used["calls"] == 3
        assert control.reserved["calls"] == 0
        with pytest.raises(BrokerAbort, match="request_drift"):
            initialize_budget(session, next_fence, BudgetVector(calls=100))


@pytest.mark.parametrize("purpose", ["process_unit", "object_storage"])
def test_neutral_nonmodel_refund_requires_domain_stop_proof(broker_fixture, purpose):
    factory, fence, _ = broker_fixture
    with neutral_transaction(factory, fence) as session:
        row = reserve_budget(
            session,
            fence,
            purpose=purpose,
            logical_key="unknown-writer",
            amounts=BudgetVector(storage_bytes=100, process_slots=1),
        )
        with pytest.raises(BrokerAbort, match="invalid_accounting"):
            settle_budget(
                session, fence, row.id, charge=BudgetVector(), outcome="not_submitted"
            )
        assert row.state == "reserved"


def test_neutral_cancellation_interrupts_real_retry_sleep(broker_fixture):
    factory, fence, _ = broker_fixture
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(controlled_retry_sleep, factory, fence, 5)
        time.sleep(0.1)
        with factory() as session:
            run = session.get(ResearchRun, fence.run_id)
            run.status = "cancelling"
            session.commit()
        with pytest.raises(BrokerAbort, match="fence_lost"):
            future.result(timeout=3)


@pytest.mark.parametrize(
    "mutation", ["caps", "reservation_amount", "delete_reservation", "projection"]
)
def test_neutral_direct_sql_cannot_rewrite_ledger(broker_fixture, mutation):
    factory, fence, _ = broker_fixture
    with neutral_transaction(factory, fence) as session:
        row = reserve_budget(
            session,
            fence,
            purpose="model_transport",
            logical_key="immutable",
            amounts=BudgetVector(calls=1),
        )
        identifier = row.id
    statements = {
        "caps": "UPDATE research_budget_controls SET limits = jsonb_set(limits, '{calls}', '999')",
        "reservation_amount": "UPDATE research_budget_reservations SET amounts = jsonb_set(amounts, '{calls}', '0') WHERE id=:id",
        "delete_reservation": "DELETE FROM research_budget_reservations WHERE id=:id",
        "projection": "UPDATE research_budget_controls SET reserved = jsonb_set(reserved, '{calls}', '0')",
    }
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(text(statements[mutation]), {"id": identifier})


def test_neutral_direct_sql_cannot_forge_completed_response_tuple(broker_fixture):
    factory, fence, settings = broker_fixture
    operation, _, request, _ = seed_operation(factory, fence, settings)
    with neutral_transaction(factory, fence) as session:
        ticket = reserve_transport(session, fence, operation, digest(request))
        ticket_id = ticket.id
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(
                """INSERT INTO research_model_outcomes
        (id,owner_id,run_id,attempt_id,generation,created_at,transport_id,outcome,usage,response_object_id,response_object_kind,response_sha256)
        SELECT :id,owner_id,run_id,attempt_id,generation,now(),id,'completed',:usage::jsonb,NULL,NULL,NULL
        FROM research_model_transports WHERE id=:transport""".replace(
                    ":usage::jsonb", "CAST(:usage AS jsonb)"
                )
            ),
            {
                "id": uuid4(),
                "transport": ticket_id,
                "usage": BudgetVector(calls=1).model_dump_json(),
            },
        )


def test_neutral_direct_sql_purpose_cannot_relabel_chat_as_embedding(broker_fixture):
    factory, fence, settings = broker_fixture
    operation, _, _, _ = seed_operation(factory, fence, settings)
    with pytest.raises(DBAPIError) as failure, factory.begin() as session:
        session.execute(
            text("""INSERT INTO research_model_operations
            (id,owner_id,run_id,attempt_id,generation,created_at,context_id,purpose,
             ordinal,request,request_object_id,request_object_kind,request_sha256,state)
            SELECT :newid,owner_id,run_id,attempt_id,generation,created_at,context_id,
             'embedding',ordinal+1,request,request_object_id,request_object_kind,
             request_sha256,'open' FROM research_model_operations WHERE id=:id"""),
            {"newid": uuid4(), "id": operation},
        )
    assert failure.value.orig.sqlstate == "23514"


def test_neutral_context_abort_persists_and_blocks_all_new_work(broker_fixture):
    from backend.app.research_broker_models import (
        ResearchContextAbort,
        ResearchModelOperation,
    )
    from backend.app.research_broker_services import abort_context, request_reservation

    factory, fence, settings = broker_fixture
    completed, context, request, ref = seed_operation(factory, fence, settings)
    response = submit_once(factory, fence, settings, completed, request, "chat")
    ids = []
    for ordinal in (1, 2, 3, 4):
        with neutral_transaction(factory, fence) as session:
            op = open_operation(
                session,
                fence,
                context,
                purpose="chat",
                ordinal=ordinal,
                request=request,
                request_object=ref,
            )
            ids.append(op.id)
    with neutral_transaction(factory, fence) as session:
        unknown = reserve_transport(session, fence, ids[0], digest(request))
        reserved = reserve_transport(session, fence, ids[1], digest(request))
        submitted = reserve_transport(session, fence, ids[2], digest(request))
        unknown_id, reserved_id, submitted_id = unknown.id, reserved.id, submitted.id
    with neutral_transaction(factory, fence) as session:
        mark_submitted(session, fence, unknown_id)
        mark_submitted(session, fence, submitted_id)
    with neutral_transaction(factory, fence) as session:
        settle_transport(
            session,
            fence,
            unknown_id,
            outcome="unknown",
            usage=request_reservation(request),
        )
        control = session.scalar(select(ResearchBudgetControl))
        before = (dict(control.used), dict(control.reserved))
    with neutral_transaction(factory, fence) as session:
        abort_id = abort_context(session, fence, context, reason="cancelled").id
    # Fresh transactions establish durable revocation, not an in-memory flag.
    with neutral_transaction(factory, fence) as session:
        assert session.get(ResearchContextAbort, abort_id).context_id == context
        assert abort_context(session, fence, context, reason="cancelled").id == abort_id
        assert session.get(ResearchModelOperation, completed).state == "completed"
        assert session.get(ResearchModelOperation, ids[0]).state == "unknown"
        assert all(
            session.get(ResearchModelOperation, i).state == "aborted" for i in ids[1:]
        )
        assert replay_completed(session, fence, completed, digest(request)) == response
        with pytest.raises(BrokerAbort, match="cancelled"):
            open_operation(
                session,
                fence,
                context,
                purpose="chat",
                ordinal=99,
                request=request,
                request_object=ref,
            )
        with pytest.raises(BrokerAbort, match="cancelled"):
            reserve_transport(session, fence, ids[3], digest(request))
        with pytest.raises(BrokerAbort, match="cancelled"):
            mark_submitted(session, fence, reserved_id)
        with pytest.raises(BrokerAbort, match="cancelled"):
            settle_transport(
                session,
                fence,
                submitted_id,
                outcome="unknown",
                usage=request_reservation(request),
            )
        control = session.scalar(select(ResearchBudgetControl))
        assert (control.used, control.reserved) == before


def test_neutral_empty_context_abort_prevents_later_first_operation(broker_fixture):
    from backend.app.research_broker_services import abort_context

    factory, fence, settings = broker_fixture
    request = ChatRequest(
        messages=(Message(role="user", content="not submitted"),), max_output_tokens=1
    )
    ref = publish_model_object(factory, fence, "model_request", request, settings)
    with neutral_transaction(factory, fence) as session:
        context_id = open_context(
            session, fence, FixtureContext(scenario="cancellation", ordinal=1)
        ).id
        abort_context(session, fence, context_id, reason="cancelled")
    with neutral_transaction(factory, fence) as session:
        with pytest.raises(BrokerAbort, match="cancelled"):
            open_operation(
                session,
                fence,
                context_id,
                purpose="chat",
                ordinal=0,
                request=request,
                request_object=ref,
            )
        assert session.scalar(select(ResearchBudgetControl)).reserved["calls"] == 0


@pytest.mark.parametrize("mutation", ["reason", "delete", "new_operation"])
def test_neutral_direct_sql_cannot_reverse_context_abort(broker_fixture, mutation):
    from backend.app.research_broker_services import abort_context

    factory, fence, settings = broker_fixture
    operation, context, _, _ = seed_operation(factory, fence, settings)
    with neutral_transaction(factory, fence) as session:
        identifier = abort_context(session, fence, context, reason="cancelled").id
    statements = {
        "reason": "UPDATE research_context_aborts SET reason='request_drift' WHERE id=:id",
        "delete": "DELETE FROM research_context_aborts WHERE id=:id",
        "new_operation": """INSERT INTO research_model_operations
          (id,owner_id,run_id,attempt_id,generation,created_at,context_id,purpose,
           ordinal,request,request_object_id,request_object_kind,request_sha256,state)
          SELECT :newid,owner_id,run_id,attempt_id,generation,created_at,context_id,purpose,
           ordinal+1,request,request_object_id,request_object_kind,request_sha256,'open'
          FROM research_model_operations WHERE id=:operation""",
    }
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(statements[mutation]),
            {"id": identifier, "operation": operation, "newid": uuid4()},
        )
