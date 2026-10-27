"""Read-only attempt containment barrier, shared by finish and recovery.

Caller owns the run/attempt locks and storage lifecycle. No live PID observation,
synthetic fence, settlement, or missing-row inference grants containment here.
"""

from uuid import UUID

from sqlalchemy import select

from backend.app.rdagent_provider_backup import (
    validate_operation_membership,
    validate_receipts,
)
from backend.app.services import DomainError
from packages.rdagent_adapter.persisted_identity import require_source_identity
from packages.rdagent_provider.codec import PROTOCOLS


def _rows(session, model, **filters):
    rows = (
        session.execute(select(model.__table__).filter_by(**filters).limit(257))
        .mappings()
        .all()
    )
    if len(rows) > 256:
        raise ValueError("Provider containment inventory exceeds bound")
    return [
        {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in row.items()
        }
        for row in rows
    ]


def provider_attempt_stopped(session, run, attempt, settings):
    """Verify every admitted SDK group and its charge; unknown stays nonterminal."""
    try:
        config = require_source_identity(run)
        if config.model_profile != "configured.rdagent.v1":
            return True  # Historical v1 has no host SDK transport protocol.
        if attempt.run_id != run.id or attempt.generation != run.generation:
            return False
        from backend.app.rdagent_models import FactorGenerationModelOperation
        from backend.app.rdagent_provider_models import RDProviderTransportReceipt
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
        operations = {
            row["id"]: row
            for row in _rows(session, ResearchModelOperation, **scope)
            if row["protocol"] in PROTOCOLS
        }
        transports = {
            row["id"]: row
            for row in _rows(session, ResearchModelTransport, **scope)
            if row["operation_id"] in operations
        }
        if any(
            row["state"] not in {"completed", "failed", "unknown"}
            for row in transports.values()
        ):
            return False
        validate_operation_membership(
            operations,
            _rows(session, FactorGenerationModelOperation, **scope),
            transports,
        )
        receipts = _rows(session, RDProviderTransportReceipt, **scope)
        reservations = {
            row["id"]: row
            for row in _rows(
                session, ResearchBudgetReservation, **scope, purpose="model_transport"
            )
        }
        settlements = {
            row["id"]: row
            for row in _rows(session, ResearchBudgetSettlement, **scope)
            if row["reservation_id"] in reservations
        }
        outcomes = {
            row["id"]: row
            for row in _rows(session, ResearchModelOutcome, **scope)
            if row["transport_id"] in transports
        }
        objects = {}
        for receipt in receipts:
            found = _rows(
                session,
                ResearchObject,
                owner_id=run.owner_id,
                id=UUID(receipt["receipt_object_id"]),
            )
            if len(found) != 1:
                return False
            objects[found[0]["id"]] = found[0]
        by_key = {obj["storage_key"]: obj for obj in objects.values()}

        def read(key, digest, cap):
            obj = by_key[key]
            if obj["sha256"] != digest or not 0 < obj["size_bytes"] <= cap:
                raise ValueError("Provider registered receipt differs")
            return _read_exact(settings.storage_root, key, digest, obj["size_bytes"])

        validate_receipts(
            receipts,
            transports=transports,
            operations=operations,
            objects=objects,
            configs={str(run.id): config},
            reservations=reservations,
            settlements=settlements,
            outcomes=outcomes,
            read_bytes=read,
        )
        return True
    except (ValueError, KeyError, OSError, DomainError):
        return False
