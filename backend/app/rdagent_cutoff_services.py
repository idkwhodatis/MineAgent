"""Disabled RD cutoff declarations; no container or snapshot authority is issued.

The v2 declaration/gate deliberately rejects launch. Provider-byte provenance,
protected numeric wrapper, v2 DB registration and exact extraction/stop closure
must land before this preparation can become an admission transition.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.factor_models import FactorVersion
from backend.app.factor_services import verify_version
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import FactorEvaluationStart, FactorGenerationSource
from backend.app.rdagent_services import _scope_row, require_research_spec
from backend.app.research_broker_services import require_reservation
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import SnapshotInput
from packages.factors.fixtures import MarketCalendar
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    candidate_inputs,
)
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
    require_v2_launch_authority,
)
from packages.research_runtime.process_manifests import InputBinding, propose_manifest


@dataclass(frozen=True)
class CandidateCutoffProposal:
    evaluation_id: UUID
    execution_id: UUID
    cutoff_ordinal: int
    prefix: PrefixManifest
    declaration: ProcessDeclarationV2
    inputs: tuple[InputBinding, ...]
    # Host-created preparation only; not a ProcessAdmission or a storage grant.


def prepare_candidate_cutoff(
    session,
    fence,
    authorization,
    *,
    evaluation_id,
    cutoff_ordinal,
    prefix,
    calendar,
    projected_files,
    reservation_id,
    settings,
):
    require_neutral_transaction(session, fence, resource_scope="processes")
    require_rd_controller(session, fence, authorization)
    _, config = require_research_spec(session, fence)
    ticket = _scope_row(session, FactorEvaluationStart, evaluation_id, fence, lock=True)
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, ticket.start_id)
    if (
        type(cutoff_ordinal) is not int
        or not 0 <= cutoff_ordinal < ticket.expected_cutoffs
        or ticket.evaluated_source_id is None
    ):
        raise DomainError(
            "factor_cutoff_scope",
            "Cutoff must belong to a source evaluation ticket",
            409,
        )
    source = _scope_row(
        session, FactorGenerationSource, ticket.evaluated_source_id, fence
    )
    snapshot = SnapshotInput.model_validate(ticket.snapshot_input)
    if (
        snapshot.sha256 != ticket.input_sha256
        or snapshot.version.factor_version_id != source.factor_version_id
        or source.factor_version_id != ticket.factor_version_id
        or source.start_id != ticket.start_id
    ):
        raise DomainError(
            "factor_cutoff_source_drift", "Cutoff source/input ticket differs", 409
        )
    calendar = MarketCalendar.model_validate_json(calendar.model_dump_json())
    prefix = PrefixManifest.model_validate_json(prefix.model_dump_json())
    sessions = [
        day
        for day in calendar.sessions
        if snapshot.window.start <= day.session_date < snapshot.window.end
    ]
    if (
        calendar.sha256 != snapshot.calendar_sha256
        or calendar.timezone != snapshot.market_timezone
        or len(sessions) != ticket.expected_cutoffs
    ):
        raise DomainError(
            "factor_cutoff_calendar_drift", "Cutoff calendar identity differs", 409
        )
    expected = sessions[cutoff_ordinal]
    index = [day.session_date for day in calendar.sessions].index(expected.session_date)
    lag_index = index + config.input.lag_sessions
    if lag_index >= len(calendar.sessions):
        raise DomainError(
            "factor_cutoff_lag_unavailable",
            "Calendar cannot close lag availability",
            409,
        )
    history = tuple(
        day.session_date
        for day in calendar.sessions[
            max(0, index - config.input.lookback_sessions) : index + 1
        ]
    )
    if (
        prefix.output_session,
        prefix.output_event_time,
        prefix.host_cutoff,
        prefix.provided_sessions,
        prefix.lag_available_at,
        prefix.candidate_image,
        prefix.normalizer_image,
        prefix.candidate_profile,
        prefix.normalizer_profile,
        prefix.max_output_rows,
        prefix.max_output_bytes,
    ) != (
        expected.session_date,
        expected.close_time,
        expected.close_time,
        history,
        calendar.sessions[lag_index].close_time,
        config.runtime.candidate_image,
        config.runtime.normalizer_image,
        config.runtime.candidate_profile,
        config.runtime.normalizer_profile,
        config.max_output_rows_per_cutoff,
        config.max_output_bytes_per_cutoff,
    ):
        raise DomainError(
            "factor_cutoff_projection_drift",
            "Host cutoff/prefix/resource identity differs",
            409,
        )
    if (
        next(
            lock.sha256
            for lock in config.runtime.dependency_locks
            if lock.role == "candidate"
        )
        != CANDIDATE_LOCK_SHA256
    ):
        raise DomainError(
            "factor_cutoff_lock_unreviewed",
            "Numeric candidate dependency closure differs",
            409,
        )
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=fence.owner_id, id=ticket.factor_version_id
        )
    )
    if version is None:
        raise DomainError(
            "factor_cutoff_source_missing", "Retained source is unavailable", 409
        )
    verify_version(session, fence.owner_id, version, settings)
    inventory, _, _ = candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=version.code.encode(),
        expected_source_sha256=source.source_sha256,
        projected_files=projected_files,
    )
    review_profile = "factor_cutoff_candidate.proposal.v1"
    manifest = propose_manifest(
        review_profile,
        config.runtime.candidate_image,
        inventory,
        trusted_image_pins={review_profile: config.runtime.candidate_image},
    )
    reservation = require_reservation(
        session,
        fence,
        reservation_id,
        purpose="process_unit",
        minimum=manifest.minimum_reservation,
    )
    if reservation.logical_key != f"rdcandidate:{ticket.id}:{cutoff_ordinal}":
        raise DomainError(
            "factor_cutoff_reservation_drift",
            "Reservation belongs to another logical cutoff",
            409,
        )
    execution_id = uuid4()
    declaration = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        role="factor_cutoff_candidate",
        logical_operation_id=execution_id,
        launch_ordinal=1,
        profile=config.runtime.candidate_profile,
        input_sha256=manifest.input_sha256,
        reservation_id=reservation_id,
        image_id=config.runtime.candidate_image,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    return CandidateCutoffProposal(
        ticket.id, execution_id, cutoff_ordinal, prefix, declaration, inventory
    )


def admit_candidate_cutoff(session, fence, proposal):
    """Hard gate before every write: disabled v2 declarations are not admissions."""
    require_neutral_transaction(session, fence, resource_scope="processes")
    require_v2_launch_authority(proposal.declaration)
    raise AssertionError("v2 launch gate must remain closed until reviewed integration")
