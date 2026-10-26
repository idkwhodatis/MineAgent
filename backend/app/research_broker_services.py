"""Flush-only neutral ledger helpers. The caller owns the fenced transaction.

Never keep these transactions open during transport or process lifetime. A
submitted transport is uncertain until durable outcome publication succeeds.
"""

from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.research_broker_models import (
    ResearchBudgetControl,
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
    ResearchContextAbort,
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
    ResearchOperationContext,
)
from packages.research_runtime.broker_contracts import (
    FixtureContext,
    ObjectRef,
    digest,
    parse_request,
)
from packages.research_runtime.budget_contracts import BudgetVector, validate_ledger
from packages.research_runtime.control import BrokerAbort


def _admit(session, fence):
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.services import DomainError

    try:
        require_neutral_transaction(session, fence)
    except DomainError:
        raise BrokerAbort("fence_lost") from None


def _values(fence):
    return {
        "owner_id": fence.owner_id,
        "run_id": fence.run_id,
        "attempt_id": fence.attempt_id,
        "generation": fence.generation,
    }


def _matches(row, fence):
    return row is not None and all(
        getattr(row, k) == v for k, v in _values(fence).items()
    )


def _budget(session, fence):
    _admit(session, fence)
    budget = session.scalar(
        select(ResearchBudgetControl)
        .where(
            ResearchBudgetControl.owner_id == fence.owner_id,
            ResearchBudgetControl.run_id == fence.run_id,
        )
        .with_for_update()
    )
    if budget is None:
        raise BrokerAbort("invalid_accounting")
    validate_ledger(budget.limits, budget.used, budget.reserved)
    return budget


def initialize_budget(session, fence, limits: BudgetVector):
    """An existing run's immutable caps are never reset by a new generation."""
    _admit(session, fence)
    row = session.scalar(
        select(ResearchBudgetControl)
        .where(
            ResearchBudgetControl.owner_id == fence.owner_id,
            ResearchBudgetControl.run_id == fence.run_id,
        )
        .with_for_update()
    )
    if row is not None:
        if row.limits != limits.model_dump():
            raise BrokerAbort("request_drift")
        return row
    row = ResearchBudgetControl(
        id=uuid4(),
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        limits=limits.model_dump(),
        used=BudgetVector().model_dump(),
        reserved=BudgetVector().model_dump(),
    )
    session.add(row)
    session.flush()
    return row


def reserve_budget(
    session, fence, *, purpose: str, logical_key: str, amounts: BudgetVector
):
    budget = _budget(session, fence)
    if purpose == "engine_mutation":
        from backend.app.rdagent_services import require_research_spec

        require_research_spec(session, fence)
        if amounts != BudgetVector(mutation_slots=1):
            raise BrokerAbort("invalid_accounting")
    if (
        purpose
        not in {"model_transport", "process_unit", "object_storage", "engine_mutation"}
        or not isinstance(logical_key, str)
        or not 1 <= len(logical_key) <= 128
        or not logical_key.isascii()
        or any(not (c.isalnum() or c in "-_:.") for c in logical_key)
    ):
        raise BrokerAbort("invalid_accounting")
    row = session.scalar(
        select(ResearchBudgetReservation)
        .filter_by(**_values(fence), purpose=purpose, logical_key=logical_key)
        .with_for_update()
    )
    if row is not None:
        if row.amounts != amounts.model_dump():
            raise BrokerAbort("request_drift")
        return row
    held = BudgetVector.model_validate(budget.reserved).add(amounts)
    if not BudgetVector.model_validate(budget.limits).covers(
        BudgetVector.model_validate(budget.used).add(held)
    ):
        raise BrokerAbort("budget_exhausted")
    row = ResearchBudgetReservation(
        id=uuid4(),
        **_values(fence),
        purpose=purpose,
        logical_key=logical_key,
        amounts=amounts.model_dump(),
        state="reserved",
    )
    budget.reserved = held.model_dump()
    session.add(row)
    session.flush()
    return row


def require_reservation(
    session, fence, reservation_id: UUID, *, purpose: str, minimum: BudgetVector
):
    _budget(session, fence)
    row = session.scalar(
        select(ResearchBudgetReservation)
        .where(ResearchBudgetReservation.id == reservation_id)
        .with_for_update()
    )
    if (
        not _matches(row, fence)
        or row.purpose != purpose
        or row.state not in {"reserved", "uncertain"}
        or not BudgetVector.model_validate(row.amounts).covers(minimum)
    ):
        raise BrokerAbort("invalid_accounting")
    return row


def mark_reservation_uncertain(session, fence, reservation_id):
    _budget(session, fence)
    row = session.scalar(
        select(ResearchBudgetReservation)
        .where(ResearchBudgetReservation.id == reservation_id)
        .with_for_update()
    )
    if not _matches(row, fence) or row.state not in {"reserved", "uncertain"}:
        raise BrokerAbort("invalid_accounting")
    row.state = "uncertain"
    session.flush()
    return row


def settle_budget(
    session, fence, reservation_id, *, charge: BudgetVector, outcome: str
):
    budget = _budget(session, fence)
    reservation = session.scalar(
        select(ResearchBudgetReservation)
        .where(ResearchBudgetReservation.id == reservation_id)
        .with_for_update()
    )
    if not _matches(reservation, fence) or outcome not in {
        "measured",
        "not_submitted",
        "conservative_unknown",
    }:
        raise BrokerAbort("invalid_accounting")
    previous = session.scalar(
        select(ResearchBudgetSettlement).where(
            ResearchBudgetSettlement.reservation_id == reservation_id
        )
    )
    if previous:
        if previous.charge != charge.model_dump() or previous.outcome != outcome:
            raise BrokerAbort("request_drift")
        return previous
    held = BudgetVector.model_validate(reservation.amounts)
    # N1 has no domain-specific non-submission/stop proof adapter. Retain the
    # full process/storage charge rather than refund on an exception or status.
    if reservation.purpose != "model_transport" and (
        charge != held or outcome != "conservative_unknown"
    ):
        raise BrokerAbort("invalid_accounting")
    if (
        not held.covers(charge)
        or (
            outcome == "not_submitted"
            and (reservation.state != "reserved" or charge != BudgetVector())
        )
        or (outcome == "conservative_unknown" and charge != held)
    ):
        raise BrokerAbort("invalid_accounting")
    budget.reserved = (
        BudgetVector.model_validate(budget.reserved).subtract(held).model_dump()
    )
    budget.used = BudgetVector.model_validate(budget.used).add(charge).model_dump()
    reservation.state = "settled"
    result = ResearchBudgetSettlement(
        id=uuid4(),
        **_values(fence),
        reservation_id=reservation_id,
        charge=charge.model_dump(),
        outcome=outcome,
    )
    session.add(result)
    session.flush()
    return result


def open_context(session, fence, context: FixtureContext):
    _budget(session, fence)
    sha = digest(context)
    row = session.scalar(
        select(ResearchOperationContext).filter_by(**_values(fence), context_sha256=sha)
    )
    if row is None:
        row = ResearchOperationContext(
            id=uuid4(),
            **_values(fence),
            context=context.model_dump(mode="json"),
            context_sha256=sha,
        )
        session.add(row)
        session.flush()
    return row


CONTEXT_ABORT_REASONS = frozenset(
    {
        "cancelled",
        "budget_exhausted",
        "publication_failed",
        "request_drift",
        "uncertain_transport",
    }
)


def _require_context_open(session, context_id):
    if (
        session.scalar(
            select(ResearchContextAbort.id).where(
                ResearchContextAbort.context_id == context_id
            )
        )
        is not None
    ):
        raise BrokerAbort("cancelled")


def abort_context(session, fence, context_id, *, reason: str):
    """Durably revoke current-fence work; retain every physical budget hold.

    This is not post-lease-loss recovery authority. Containment may continue in
    its separate supervisor path, but cannot use this API to create new work.
    """
    _budget(session, fence)
    context = session.get(ResearchOperationContext, context_id)
    if not _matches(context, fence) or reason not in CONTEXT_ABORT_REASONS:
        raise BrokerAbort("invalid_accounting")
    existing = session.scalar(
        select(ResearchContextAbort).where(
            ResearchContextAbort.context_id == context_id
        )
    )
    if existing is not None:
        if existing.reason != reason:
            raise BrokerAbort("request_drift")
        return existing
    abort = ResearchContextAbort(
        id=uuid4(), **_values(fence), context_id=context_id, reason=reason
    )
    session.add(abort)
    for operation in session.scalars(
        select(ResearchModelOperation)
        .where(
            ResearchModelOperation.context_id == context_id,
            ResearchModelOperation.state == "open",
        )
        .with_for_update()
    ):
        operation.state = "aborted"
    session.flush()
    return abort


def _object(session, owner_id, ref: ObjectRef, kind):
    from backend.app.research_object_models import ResearchObject

    row = session.get(ResearchObject, ref.id)
    if (
        row is None
        or ref.kind != kind
        or row.owner_id != owner_id
        or row.object_kind != kind
        or row.sha256 != ref.sha256
    ):
        raise BrokerAbort("publication_failed")
    return row


def open_operation(
    session,
    fence,
    context_id,
    *,
    purpose: str,
    ordinal: int,
    request,
    request_object: ObjectRef,
):
    _budget(session, fence)
    context = session.get(ResearchOperationContext, context_id)
    if (
        not _matches(context, fence)
        or purpose not in {"chat", "embedding", "continuation"}
        or type(ordinal) is not int
        or not 0 <= ordinal <= 1000000
    ):
        raise BrokerAbort("invalid_accounting")
    _require_context_open(session, context_id)
    from backend.app.models import ResearchRun
    from backend.app.research_lifecycle import require_legacy_run

    require_legacy_run(session.get(ResearchRun, fence.run_id))
    typed = parse_request(request.model_dump(mode="json"))
    if (purpose == "embedding") != (typed.kind == "embedding"):
        raise BrokerAbort("request_drift")
    sha = digest(typed)
    if request_object.sha256 != sha:
        raise BrokerAbort("request_drift")
    _object(session, fence.owner_id, request_object, "model_request")
    row = session.scalar(
        select(ResearchModelOperation)
        .where(
            ResearchModelOperation.context_id == context_id,
            ResearchModelOperation.purpose == purpose,
            ResearchModelOperation.ordinal == ordinal,
        )
        .with_for_update()
    )
    if row is not None:
        if (
            row.request_sha256 != sha
            or row.request != typed.model_dump(mode="json")
            or row.request_object_id != request_object.id
        ):
            raise BrokerAbort("request_drift")
        return row
    row = ResearchModelOperation(
        id=uuid4(),
        **_values(fence),
        context_id=context_id,
        protocol="mineagent.neutral-model.v1",
        purpose=purpose,
        ordinal=ordinal,
        request=typed.model_dump(mode="json"),
        request_sha256=sha,
        request_object_id=request_object.id,
        request_object_kind="model_request",
        state="open",
    )
    session.add(row)
    session.flush()
    return row


def request_reservation(request) -> BudgetVector:
    """Fixture-only upper bound; no assumed pricing for any external model."""
    typed = parse_request(
        request if isinstance(request, dict) else request.model_dump(mode="json")
    )
    if typed.kind == "chat":
        tokens = (
            sum(len(m.content.encode()) for m in typed.messages)
            + typed.max_output_tokens
        )
    else:
        tokens = sum(len(s.encode()) for s in typed.inputs)
    return BudgetVector(calls=1, tokens=tokens)


def _versioned_reservation(operation):
    from packages.rdagent_adapter.persisted_broker import (
        request_reservation as versioned,
    )

    return versioned(operation.protocol, operation.request)


def _versioned_response(operation, response):
    from packages.rdagent_adapter.persisted_broker import (
        parse_request as versioned_request,
    )
    from packages.rdagent_adapter.persisted_broker import (
        validate_response as versioned_response,
    )

    return versioned_response(
        operation.protocol,
        versioned_request(operation.protocol, operation.request),
        response,
    )


def reserve_transport(session, fence, operation_id, expected_request_sha256: str):
    _budget(session, fence)
    operation = session.scalar(
        select(ResearchModelOperation)
        .where(ResearchModelOperation.id == operation_id)
        .with_for_update()
    )
    if (
        not _matches(operation, fence)
        or operation.request_sha256 != expected_request_sha256
    ):
        raise BrokerAbort("request_drift")
    _require_context_open(session, operation.context_id)
    if operation.state != "open":
        raise BrokerAbort("uncertain_transport")
    latest = session.scalar(
        select(ResearchModelTransport)
        .where(ResearchModelTransport.operation_id == operation_id)
        .order_by(ResearchModelTransport.ordinal.desc())
        .limit(1)
        .with_for_update()
    )
    if latest is not None and latest.state != "failed":
        raise BrokerAbort("uncertain_transport")
    ordinal = 1 if latest is None else latest.ordinal + 1
    reservation = reserve_budget(
        session,
        fence,
        purpose="model_transport",
        logical_key=f"transport:{operation_id}:{ordinal}",
        amounts=_versioned_reservation(operation),
    )
    transport = ResearchModelTransport(
        id=uuid4(),
        **_values(fence),
        operation_id=operation_id,
        ordinal=ordinal,
        reservation_id=reservation.id,
        state="reserved",
    )
    session.add(transport)
    session.flush()
    return transport


def mark_submitted(session, fence, transport_id):
    _budget(session, fence)
    transport = session.scalar(
        select(ResearchModelTransport)
        .where(ResearchModelTransport.id == transport_id)
        .with_for_update()
    )
    if not _matches(transport, fence) or transport.state != "reserved":
        raise BrokerAbort("uncertain_transport")
    operation = session.get(ResearchModelOperation, transport.operation_id)
    _require_context_open(session, operation.context_id)
    mark_reservation_uncertain(session, fence, transport.reservation_id)
    transport.state = "submitted"
    session.flush()
    return transport


def settle_transport(
    session,
    fence,
    transport_id,
    *,
    outcome: str,
    usage: BudgetVector,
    response_object: ObjectRef | None = None,
    response=None,
):
    _budget(session, fence)
    transport = session.get(ResearchModelTransport, transport_id)
    if not _matches(transport, fence) or outcome not in {
        "completed",
        "failed",
        "unknown",
    }:
        raise BrokerAbort("invalid_accounting")
    previous = session.scalar(
        select(ResearchModelOutcome).where(
            ResearchModelOutcome.transport_id == transport_id
        )
    )
    if previous:
        expected = None if response_object is None else response_object.id
        if (
            previous.outcome != outcome
            or previous.usage != usage.model_dump()
            or previous.response_object_id != expected
        ):
            raise BrokerAbort("request_drift")
        return previous
    if transport.state != "submitted" or usage.calls != 1:
        raise BrokerAbort("invalid_accounting")
    operation = session.get(ResearchModelOperation, transport.operation_id)
    _require_context_open(session, operation.context_id)
    if outcome == "completed":
        if response is None:
            raise BrokerAbort("publication_failed")
        _versioned_response(operation, response)
        if response_object is None or digest(response) != response_object.sha256:
            raise BrokerAbort("publication_failed")
        _object(session, fence.owner_id, response_object, "model_response")
        operation.state = "completed"
    elif response_object is not None or response is not None:
        raise BrokerAbort("invalid_accounting")
    if outcome == "unknown":
        operation.state = "unknown"
    settlement = settle_budget(
        session,
        fence,
        transport.reservation_id,
        charge=usage,
        outcome="conservative_unknown" if outcome == "unknown" else "measured",
    )
    row = ResearchModelOutcome(
        id=uuid4(),
        **_values(fence),
        transport_id=transport.id,
        outcome=outcome,
        usage=settlement.charge,
        response_object_id=None if response_object is None else response_object.id,
        response_object_kind=None if response_object is None else "model_response",
        response_sha256=None if response_object is None else response_object.sha256,
    )
    transport.state = outcome
    session.add(row)
    session.flush()
    return row


def replay_completed(session, fence, operation_id, request_sha256: str):
    """Read exact previous-generation completion without granting work authority."""
    _budget(session, fence)
    operation = session.get(ResearchModelOperation, operation_id)
    if (
        operation is None
        or operation.owner_id != fence.owner_id
        or operation.run_id != fence.run_id
        or operation.generation > fence.generation
        or operation.request_sha256 != request_sha256
    ):
        raise BrokerAbort("request_drift")
    if operation.state != "completed":
        raise BrokerAbort("uncertain_transport")
    outcome = session.scalar(
        select(ResearchModelOutcome)
        .join(
            ResearchModelTransport,
            ResearchModelTransport.id == ResearchModelOutcome.transport_id,
        )
        .where(
            ResearchModelTransport.operation_id == operation_id,
            ResearchModelOutcome.outcome == "completed",
        )
    )
    if outcome is None:
        raise BrokerAbort("invalid_accounting")
    ref = ObjectRef(
        id=outcome.response_object_id,
        kind="model_response",
        sha256=outcome.response_sha256,
    )
    _object(session, fence.owner_id, ref, "model_response")
    return ref


def validate_accounting_closure(tables, indexes, *, rd_source=False):
    """Validate restored evidence without launching, refunding or redispatching."""
    from collections import defaultdict

    from packages.research_runtime.broker_contracts import FixtureContext

    def rows(name):
        return tables[name]

    def lookup(name, identifier):
        candidates = indexes[name]
        result = candidates.get(identifier) or candidates.get(str(identifier))
        if result is None:
            # JSON inventories commonly stringify UUID keys while in-memory
            # integration snapshots may preserve native UUIDs.
            result = next(
                (v for k, v in candidates.items() if str(k) == str(identifier)), None
            )
        if result is None:
            raise ValueError("Missing accounting reference")
        return result

    def same(left, right):
        if any(
            str(left[k]) != str(right[k])
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        ):
            raise ValueError("Accounting reference escaped its exact attempt")

    def obj(row, prefix, kind):
        target = lookup("research_objects", row[f"{prefix}_object_id"])
        if (
            str(target["owner_id"]) != str(row["owner_id"])
            or target["object_kind"] != kind
            or row[f"{prefix}_object_kind"] != kind
            or target["sha256"] != row[f"{prefix}_sha256"]
        ):
            raise ValueError("Accounting object identity mismatch")

    controls = {
        (str(r["owner_id"]), str(r["run_id"])): r
        for r in rows("research_budget_controls")
    }
    if len(controls) != len(rows("research_budget_controls")):
        raise ValueError("Duplicate run budget")
    spent, held = defaultdict(BudgetVector), defaultdict(BudgetVector)
    settlements = {}
    for settlement in rows("research_budget_settlements"):
        key = str(settlement["reservation_id"])
        if key in settlements:
            raise ValueError("Duplicate budget settlement")
        reservation = lookup(
            "research_budget_reservations", settlement["reservation_id"]
        )
        same(settlement, reservation)
        amount, charge = (
            BudgetVector.model_validate(reservation["amounts"]),
            BudgetVector.model_validate(settlement["charge"]),
        )
        outcome = settlement["outcome"]
        if (
            outcome not in {"measured", "not_submitted", "conservative_unknown"}
            or not amount.covers(charge)
            or (outcome == "not_submitted" and charge != BudgetVector())
            or (outcome == "conservative_unknown" and charge != amount)
        ):
            raise ValueError("Invalid settlement usage")
        if reservation["purpose"] != "model_transport" and (
            outcome != "conservative_unknown" or charge != amount
        ):
            raise ValueError("Unproven process/storage refund")
        settlements[key] = settlement
    reservation_keys = set()
    for reservation in rows("research_budget_reservations"):
        identity = tuple(
            str(reservation[k])
            for k in (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "purpose",
                "logical_key",
            )
        )
        allowed_purposes = {"model_transport", "process_unit", "object_storage"}
        if rd_source:
            allowed_purposes.add("engine_mutation")
        if (
            identity in reservation_keys
            or reservation["purpose"] not in allowed_purposes
        ):
            raise ValueError("Invalid reservation identity")
        reservation_keys.add(identity)
        key = (str(reservation["owner_id"]), str(reservation["run_id"]))
        if key not in controls:
            raise ValueError("Reservation has no immutable run budget")
        amount = BudgetVector.model_validate(reservation["amounts"])
        settlement = settlements.get(str(reservation["id"]))
        if reservation["state"] == "settled":
            if settlement is None:
                raise ValueError("Settled reservation has no evidence")
            spent[key] = spent[key].add(
                BudgetVector.model_validate(settlement["charge"])
            )
        elif reservation["state"] in {"reserved", "uncertain"}:
            if settlement is not None:
                raise ValueError("Unresolved reservation already settled")
            held[key] = held[key].add(amount)
        else:
            raise ValueError("Unsupported reservation state")
    for key, control in controls.items():
        validate_ledger(control["limits"], control["used"], control["reserved"])
        if (
            control["used"] != spent[key].model_dump()
            or control["reserved"] != held[key].model_dump()
        ):
            raise ValueError("Budget projections do not conserve immutable ledger")
    context_keys = set()
    for context in rows("research_operation_contexts"):
        if rd_source:
            from packages.rdagent_adapter.persisted_broker import validate_context

            typed = validate_context(context["context"])
        else:
            typed = FixtureContext.model_validate(context["context"])
        if digest(typed) != context["context_sha256"]:
            raise ValueError("Context canonical hash mismatch")
        key = tuple(
            str(context[k])
            for k in (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "context_sha256",
            )
        )
        if key in context_keys:
            raise ValueError("Duplicate immutable context")
        context_keys.add(key)
    aborted_contexts = set()
    for abort in rows("research_context_aborts"):
        context_id = str(abort["context_id"])
        same(abort, lookup("research_operation_contexts", abort["context_id"]))
        if (
            context_id in aborted_contexts
            or abort["reason"] not in CONTEXT_ABORT_REASONS
        ):
            raise ValueError("Invalid durable context revocation")
        aborted_contexts.add(context_id)
    operations = rows("research_model_operations")
    operation_keys = set()
    for operation in operations:
        same(operation, lookup("research_operation_contexts", operation["context_id"]))
        revoked = str(operation["context_id"]) in aborted_contexts
        if (revoked and operation["state"] == "open") or (
            operation["state"] == "aborted" and not revoked
        ):
            raise ValueError("Operation disagrees with durable context revocation")
        if rd_source:
            from packages.rdagent_adapter.persisted_broker import (
                parse_request as versioned,
            )

            request = versioned(operation["protocol"], operation["request"])
        else:
            request = parse_request(operation["request"])
        if (
            digest(request) != operation["request_sha256"]
            or operation["purpose"] not in {"chat", "embedding", "continuation"}
            or ((operation["purpose"] == "embedding") != (request.kind == "embedding"))
            or type(operation["ordinal"]) is not int
            or not 0 <= operation["ordinal"] <= 1000000
        ):
            raise ValueError("Operation request drift")
        key = (str(operation["context_id"]), operation["purpose"], operation["ordinal"])
        if key in operation_keys:
            raise ValueError("Duplicate logical operation")
        operation_keys.add(key)
        obj(operation, "request", "model_request")
    outcomes = {}
    for outcome in rows("research_model_outcomes"):
        key = str(outcome["transport_id"])
        if key in outcomes:
            raise ValueError("Duplicate transport outcome")
        transport = lookup("research_model_transports", outcome["transport_id"])
        same(outcome, transport)
        settlement = settlements.get(str(transport["reservation_id"]))
        if (
            settlement is None
            or settlement["charge"] != outcome["usage"]
            or outcome["outcome"] != transport["state"]
            or outcome["outcome"] not in {"completed", "failed", "unknown"}
            or BudgetVector.model_validate(outcome["usage"]).calls != 1
        ):
            raise ValueError("Transport outcome/accounting mismatch")
        if outcome["outcome"] == "completed":
            obj(outcome, "response", "model_response")
        elif any(
            outcome[f"response_{suffix}"] is not None
            for suffix in ("object_id", "object_kind", "sha256")
        ):
            raise ValueError("Failed transport contains a response")
        if (outcome["outcome"] == "unknown") != (
            settlement["outcome"] == "conservative_unknown"
        ):
            raise ValueError("Unknown usage is not conservatively retained")
        outcomes[key] = outcome
    by_operation = defaultdict(list)
    used_reservations = set()
    for transport in rows("research_model_transports"):
        operation = lookup("research_model_operations", transport["operation_id"])
        reservation = lookup(
            "research_budget_reservations", transport["reservation_id"]
        )
        same(transport, operation)
        same(transport, reservation)
        if (
            reservation["purpose"] != "model_transport"
            or str(reservation["id"]) in used_reservations
            or reservation["amounts"]
            != request_reservation(operation["request"]).model_dump()
        ):
            raise ValueError("Physical transport reservation mismatch")
        used_reservations.add(str(reservation["id"]))
        state = transport["state"]
        if state not in {"reserved", "submitted", "completed", "failed", "unknown"}:
            raise ValueError("Invalid transport state")
        if (state in {"completed", "failed", "unknown"}) != (
            str(transport["id"]) in outcomes
        ):
            raise ValueError("Missing or premature transport outcome")
        if (
            state == "reserved"
            and reservation["state"] != "reserved"
            or state == "submitted"
            and reservation["state"] != "uncertain"
        ):
            raise ValueError("Submission reservation state mismatch")
        by_operation[str(operation["id"])].append(transport)
    for operation in operations:
        chain = sorted(by_operation[str(operation["id"])], key=lambda t: t["ordinal"])
        if [t["ordinal"] for t in chain] != list(range(1, len(chain) + 1)) or any(
            t["state"] != "failed" for t in chain[:-1]
        ):
            raise ValueError("Transport retry sequence is not explicit")
        latest = chain[-1]["state"] if chain else None
        state = operation["state"]
        if (
            state not in {"open", "completed", "unknown", "aborted"}
            or (state == "completed") != (latest == "completed")
            or (state == "unknown") != (latest == "unknown")
        ):
            raise ValueError("Operation terminal projection is inconsistent")
