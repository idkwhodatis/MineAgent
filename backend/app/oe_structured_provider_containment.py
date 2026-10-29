"""Read-only normal027 SDK stop/charge barrier after revocation.

Caller owns run/attempt locks and storage lifecycle. This never grants a live
fence, observes/kills a PID, settles a hold, or infers provider delivery from intent.
"""

from uuid import UUID

from sqlalchemy import select

from backend.app.oe_structured_backup import (
    _full_charge,
    _registered_read,
    _same,
    validate_provider_inventory,
)
from backend.app.oe_structured_identity import require_run_identity
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_accounting import (
    request_reservation,
    validate_completed_usage,
)
from packages.openevolve_structured.provider_contracts import (
    OEChatRequest,
    OEChatResponse,
)
from packages.openevolve_structured.provider_receipts import (
    OBJECT_KIND,
    parse_receipt,
    validate_chain,
)
from packages.openevolve_structured.provider_response import response_bytes
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError


def _rows(session, model, **filters):
    rows = (
        session.execute(select(model.__table__).filter_by(**filters).limit(33))
        .mappings()
        .all()
    )
    if len(rows) > 32:
        raise ValueError("Normal OE SDK barrier inventory exceeds fixed bound")
    return [
        {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in row.items()
        }
        for row in rows
    ]


def _group(member, shared, receipts, binding, read):
    operation = shared["research_model_operations"][member["operation_id"]]
    transport = shared["research_model_transports"][member["transport_id"]]
    if (
        not _same(member, operation)
        or not _same(member, transport)
        or operation["protocol"] != "mineagent.oe-structured-provider-chat.v1"
        or operation["purpose"] != "chat"
        or operation["ordinal"] != 0
        or operation["state"] not in {"completed", "unknown", "aborted"}
        or operation["request_object_kind"] != "oe_structured_provider_request"
        or transport["operation_id"] != operation["id"]
        or transport["ordinal"] != 1
        or transport["state"] not in {"completed", "failed", "unknown"}
        or (transport["state"] == "completed") != (operation["state"] == "completed")
        or member["model_binding_sha256"] != binding.sha256
        or member["request_sha256"] != operation["request_sha256"]
    ):
        raise ValueError("Normal OE original terminal SDK identity differs")
    raw = _registered_read(
        shared,
        read,
        member["owner_id"],
        operation["request_object_id"],
        operation["request_object_kind"],
        operation["request_sha256"],
    )
    request = OEChatRequest.model_validate_json(raw)
    if (
        canonical_bytes(request) != raw
        or request.model_dump(mode="json") != operation["request"]
    ):
        raise ValueError("Normal OE original request differs")
    held = shared["research_budget_reservations"][transport["reservation_id"]]
    if (
        not _same(member, held)
        or held["purpose"] != "model_transport"
        or held["logical_key"]
        != "oe-structured-provider:" + member["iteration_intent_id"]
        or BudgetVector.model_validate(held["amounts"])
        != request_reservation(request, binding)
    ):
        raise ValueError("Normal OE original SDK hold differs")
    _full_charge(member, shared, held["id"])
    documents = {}
    for row in receipts:
        if row["membership_id"] != member["id"]:
            continue
        if (
            not _same(member, row)
            or row["receipt_object_kind"] != OBJECT_KIND
            or row["kind"] in documents
        ):
            raise ValueError("Normal OE duplicate/foreign SDK receipt")
        raw = _registered_read(
            shared,
            read,
            member["owner_id"],
            row["receipt_object_id"],
            OBJECT_KIND,
            row["receipt_sha256"],
        )
        document = parse_receipt(raw)
        if (
            document.kind != row["kind"]
            or len(raw) != row["size_bytes"]
            or str(document.transport_id) != transport["id"]
            or document.request_sha256 != request.sha256
            or document.model_binding_sha256 != binding.sha256
            or any(
                str(getattr(document, k)) != str(member[k])
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            )
        ):
            raise ValueError("Normal OE actual SDK receipt binding differs")
        documents[document.kind] = document
    if (
        set(documents) != {"intent", "started", "stopped"}
        or documents["intent"].maximum_duration_ms != binding.request_deadline_ms
    ):
        raise ValueError("Normal OE actual complete SDK stop chain required")
    validate_chain(
        documents["intent"],
        documents["started"],
        documents["stopped"],
        completed=transport["state"] == "completed",
    )
    outcomes = [
        r
        for r in shared["research_model_outcomes"].values()
        if r["transport_id"] == transport["id"]
    ]
    if len(outcomes) != 1 or not _same(member, outcomes[0]):
        raise ValueError("Normal OE terminal SDK outcome missing")
    outcome = outcomes[0]
    if BudgetVector.model_validate(outcome["usage"]) != BudgetVector.model_validate(
        held["amounts"]
    ):
        raise ValueError(
            "Normal OE terminal outcome must retain original conservative usage"
        )
    if outcome["outcome"] != transport["state"]:
        raise ValueError("Normal OE terminal SDK outcome differs")
    if transport["state"] == "completed":
        if outcome["response_object_kind"] != "oe_structured_provider_response":
            raise ValueError("Normal OE response kind differs")
        raw = _registered_read(
            shared,
            read,
            member["owner_id"],
            outcome["response_object_id"],
            outcome["response_object_kind"],
            outcome["response_sha256"],
        )
        response = OEChatResponse.model_validate_json(raw)
        if response_bytes(response) != raw:
            raise ValueError("Normal OE response canonical bytes differ")
        _, expected = validate_completed_usage(request, response, binding)
        if BudgetVector.model_validate(outcome["usage"]) != expected:
            raise ValueError("Normal OE original usage differs")
    elif any(
        outcome[k] is not None
        for k in ("response_object_id", "response_object_kind", "response_sha256")
    ):
        raise ValueError("Unsuccessful SDK outcome cannot invent completed response")


def provider_attempt_stopped(session, run, attempt, settings):
    try:
        config = require_run_identity(run)
        if (
            attempt.run_id != run.id
            or attempt.owner_id != run.owner_id
            or attempt.generation != run.generation
        ):
            return False
        from backend.app.oe_structured_operational_models import (
            OEStructuredProviderReceipt,
            OEStructuredProviderTransport,
        )
        from backend.app.research_broker_models import (
            ResearchBudgetReservation,
            ResearchBudgetSettlement,
            ResearchModelOperation,
            ResearchModelOutcome,
            ResearchModelTransport,
        )
        from backend.app.research_object_models import ResearchObject
        from backend.app.research_object_services import _read_exact

        scope = {
            "owner_id": run.owner_id,
            "run_id": run.id,
            "attempt_id": attempt.id,
            "generation": attempt.generation,
        }

        def scoped_rows(model, **extra):
            values = _rows(
                session, model, owner_id=run.owner_id, run_id=run.id, **extra
            )
            if any(
                any(str(row[key]) != str(value) for key, value in scope.items())
                for row in values
            ):
                raise ValueError("Normal OE initial-attempt inventory scope differs")
            return values

        shared = {}
        for model in (
            ResearchModelOperation,
            ResearchModelTransport,
            ResearchModelOutcome,
            ResearchBudgetReservation,
        ):
            filters = (
                {"purpose": "model_transport"}
                if model is ResearchBudgetReservation
                else {}
            )
            values = scoped_rows(model, **filters)
            shared[model.__tablename__] = {r["id"]: r for r in values}
        shared["research_budget_settlements"] = {}
        for reservation_id in shared["research_budget_reservations"]:
            for row in scoped_rows(
                ResearchBudgetSettlement, reservation_id=UUID(reservation_id)
            ):
                shared["research_budget_settlements"][row["id"]] = row
        members = {r["id"]: r for r in scoped_rows(OEStructuredProviderTransport)}
        receipts = scoped_rows(OEStructuredProviderReceipt)
        validate_provider_inventory({str(run.id)}, members, shared)
        if len(members) > config.search.maximum_mutation_slots or any(
            r["membership_id"] not in members for r in receipts
        ):
            return False
        transports = shared["research_model_transports"]
        if {
            r["transport_id"] for r in shared["research_model_outcomes"].values()
        } != set(transports):
            return False
        if {r["reservation_id"] for r in transports.values()} != set(
            shared["research_budget_reservations"]
        ):
            return False
        object_ids = (
            {r["receipt_object_id"] for r in receipts}
            | {
                r["request_object_id"]
                for r in shared["research_model_operations"].values()
            }
            | {
                r["response_object_id"]
                for r in shared["research_model_outcomes"].values()
                if r["response_object_id"] is not None
            }
        )
        objects = {}
        for identity in object_ids:
            found = _rows(
                session, ResearchObject, owner_id=run.owner_id, id=UUID(identity)
            )
            if len(found) != 1:
                return False
            objects[identity] = found[0]
        shared["research_objects"] = objects
        by_key = {r["storage_key"]: r for r in objects.values()}

        def read(key, sha, size):
            row = by_key[key]
            if (row["sha256"], row["size_bytes"]) != (sha, size):
                raise ValueError("Normal OE exact original object differs")
            return _read_exact(settings.storage_root, key, sha, size)

        for member in members.values():
            _group(member, shared, receipts, config.model_binding, read)
        return True
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        DomainError,
        ProcessError,
    ):
        return False
