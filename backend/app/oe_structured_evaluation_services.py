"""Normal027 live evaluation callback: declaration→one unit→fresh stopped result.

A deterministic ticket detects wire drift; it is not launch authority. Every
boundary still resolves the actual candidate, controller/worker peer and owned
inputs. An existing uncertain admission is never retried or converted to a score.
"""

import hashlib
import hmac
from uuid import UUID

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_candidate_registration import register_candidate
from backend.app.oe_structured_candidate_services import require_candidate_execution
from backend.app.oe_structured_controller_context import scoped
from backend.app.oe_structured_engine_process import execute_candidate_engine
from backend.app.oe_structured_engine_results import (
    persist_candidate_engine_outcome,
    read_candidate_engine_outcome,
)
from backend.app.oe_structured_engine_services import fail, require_execution
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredEngineOutcome,
)
from backend.app.oe_structured_population import measurement_feedback
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import _raw_fields
from packages.openevolve_structured.contracts import EvaluationGrant, EvaluationResponse


def _grant(session, fence, candidate_id, execution_id, settings, *, peer, ticket_key):
    if type(ticket_key) is not bytes or len(ticket_key) != 32:
        fail("oe_structured_evaluation_ticket_key")
    authority = require_candidate_execution(
        session, fence, candidate_id, settings, peer=peer
    )
    pins = require_execution(session, fence, execution_id, settings, peer=peer)
    candidate = scoped(session.get(OEStructuredCandidate, candidate_id), fence)
    if pins.candidate_id != candidate.id or authority.candidate_id != candidate.id:
        fail("oe_structured_evaluation_identity")
    values = {
        "owner_id": fence.owner_id,
        "run_id": fence.run_id,
        "epoch_id": candidate.epoch_id,
        "candidate_id": candidate.id,
        "upstream_program_id": candidate.upstream_program_id,
        "candidate_sha256": authority.candidate_sha256,
        "program_sha256": authority.program_sha256,
        "execution_id": execution_id,
        "execution_binding_sha256": authority.config.search.execution_binding_sha256,
        "phase": "seed" if candidate.ordinal == 0 else "mutation",
    }
    token = hmac.new(
        ticket_key,
        b"mineagent.oe-structured-evaluation-ticket.v1\0"
        + canonical_bytes(
            {
                key: str(value) if type(value) is UUID else value
                for key, value in values.items()
            }
        ),
        hashlib.sha256,
    ).hexdigest()
    return EvaluationGrant(**values, ticket=token)


def register_evaluation(
    factory,
    fence,
    epoch_id,
    upstream_program_id,
    source_bytes,
    settings,
    *,
    peer,
    ticket_key,
):
    """Commit the actual source/program/input declaration before returning a grant."""
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            candidate, execution_id = register_candidate(
                session,
                fence,
                epoch_id,
                upstream_program_id,
                source_bytes,
                settings,
                peer=peer,
            )
            grant = _grant(
                session,
                fence,
                candidate.id,
                execution_id,
                settings,
                peer=peer,
                ticket_key=ticket_key,
            )
        # Returning from inside the transaction would obscure commit-before-ACK.
        return grant


def _checked_grant(session, fence, supplied, settings, *, peer, ticket_key):
    if type(supplied) is not EvaluationGrant:
        fail("oe_structured_evaluation_grant")
    supplied = EvaluationGrant.model_validate(_raw_fields(supplied))
    actual = _grant(
        session,
        fence,
        supplied.candidate_id,
        supplied.execution_id,
        settings,
        peer=peer,
        ticket_key=ticket_key,
    )
    if actual != supplied:
        fail("oe_structured_evaluation_grant")
    return actual


def _response(session, fence, grant, settings, *, peer):
    verified = read_candidate_engine_outcome(
        session, fence, grant.execution_id, settings, peer=peer
    )
    row = scoped(
        session.scalar(
            select(OEStructuredEngineOutcome).filter_by(execution_id=grant.execution_id)
        ),
        fence,
    )
    measurement = verified.measurement
    if (
        verified.candidate_id != grant.candidate_id
        or verified.execution_id != grant.execution_id
        or row.candidate_id != grant.candidate_id
        or row.result_sha256 != verified.result_sha256
        or row.measurement_sha256 != digest(measurement)
    ):
        fail("oe_structured_evaluation_outcome")
    return EvaluationResponse(
        candidate_id=grant.candidate_id,
        candidate_sha256=grant.candidate_sha256,
        program_sha256=grant.program_sha256,
        execution_id=grant.execution_id,
        execution_binding_sha256=grant.execution_binding_sha256,
        result_sha256=verified.result_sha256,
        objective_sha256=measurement.objective_sha256,
        objective_receipt_id=row.id,
        eligible=measurement.eligible,
        combined_score=measurement.combined_score,
        reason=measurement.reason,
        feedback=measurement_feedback(measurement),
    )


def execute_evaluation(factory, fence, grant, settings, *, peer, ticket_key):
    """One real protected engine unit, or fresh physical read of its exact outcome.

    No transaction spans Docker. Native issuer refuses any existing admission,
    including a stopped-but-unpublished one; recovery is not a hidden re-launch.
    Failure propagates without an EvaluationResponse or invented measurement.
    """
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with (
            native_storage_guard(settings.storage_root),
            neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session,
        ):
            actual = _checked_grant(
                session, fence, grant, settings, peer=peer, ticket_key=ticket_key
            )
            previous = session.scalar(
                select(OEStructuredEngineOutcome).filter_by(
                    execution_id=actual.execution_id
                )
            )
            if previous is not None:
                response = _response(session, fence, actual, settings, peer=peer)
                replay = True
            else:
                replay = False
        if replay:
            return response
        admission_id = execute_candidate_engine(
            factory, fence, actual.execution_id, settings, peer=peer
        )
        with (
            native_storage_guard(settings.storage_root),
            neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session,
        ):
            _checked_grant(
                session, fence, actual, settings, peer=peer, ticket_key=ticket_key
            )
            persist_candidate_engine_outcome(
                session, fence, actual.execution_id, admission_id, settings, peer=peer
            )
            response = _response(session, fence, actual, settings, peer=peer)
        return response


def read_evaluation_response(session, fence, grant, settings, *, peer, ticket_key):
    """Flush-free live ACK reconstruction, never permission to execute a unit."""
    actual = _checked_grant(
        session, fence, grant, settings, peer=peer, ticket_key=ticket_key
    )
    return _response(session, fence, actual, settings, peer=peer)
