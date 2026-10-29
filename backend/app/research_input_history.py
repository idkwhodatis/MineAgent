"""Owned bounded derivation history, without disclosing raw external content."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from backend.app.research_input_derivations import events_for
from backend.app.research_input_models import ResearchInputDerivation
from backend.app.research_input_storage import require_input_capability
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.derivation import validate_event_prefix
from packages.research_inputs.errors import PublicDerivationErrorCode, public_error_code


@dataclass(frozen=True)
class DerivationHistory:
    derivation_id: UUID
    workflow_id: UUID
    stage_id: UUID
    operation: str
    source_sha256: str
    raw_object_id: UUID | None
    raw_input_sha256: str | None
    mapping_sha256: str | None
    status: str
    error_code: PublicDerivationErrorCode | None
    prepared_at: datetime
    started_at: datetime | None
    terminal_event_id: UUID | None
    terminal_event_sha256: str | None
    terminal_at: datetime | None
    outputs: tuple[InputVersionRef, ...]


def read_derivation_history(session, owner_id, workflow_id, stage_id):
    """Missing receipt is missing, never a manufactured prepared/ready state.

    No raw bytes, arbitrary exception prose, or normalized text is returned.
    Actual normalized text requires the separately fresh owned resolver.
    """
    require_input_capability(session)
    row = session.scalar(
        select(ResearchInputDerivation).where(
            ResearchInputDerivation.owner_id == owner_id,
            ResearchInputDerivation.workflow_id == workflow_id,
            ResearchInputDerivation.stage_id == stage_id,
        )
    )
    if row is None:
        raise DomainError("not_found", "Input derivation not found", 404)
    if (row.owner_id, row.workflow_id, row.stage_id) != (
        owner_id,
        workflow_id,
        stage_id,
    ):
        raise DomainError(
            "input_derivation_changed", "Input derivation scope differs", 409
        )
    try:
        events = events_for(session, row)
    except (ValueError, TypeError):
        raise DomainError(
            "input_derivation_changed", "Input derivation receipt is inconsistent", 409
        ) from None
    if not events:
        raise DomainError(
            "input_derivation_changed", "Input derivation receipt is missing", 409
        )
    status = validate_event_prefix(events)
    terminal = events[-1] if events[-1].state not in {"prepared", "started"} else None
    started = next((event for event in events if event.state == "started"), None)
    return DerivationHistory(
        derivation_id=row.id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        operation=row.operation,
        source_sha256=row.source_sha256,
        raw_object_id=row.raw_object_id,
        raw_input_sha256=row.raw_object_sha256,
        mapping_sha256=row.mapping_sha256,
        status=status,
        error_code=public_error_code(None if terminal is None else terminal.error_code),
        prepared_at=events[0].recorded_at,
        started_at=None if started is None else started.recorded_at,
        terminal_event_id=None if terminal is None else terminal.id,
        terminal_event_sha256=None if terminal is None else digest(terminal),
        terminal_at=None if terminal is None else terminal.recorded_at,
        outputs=() if terminal is None else terminal.outputs,
    )
