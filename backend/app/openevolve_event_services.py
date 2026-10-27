"""Flush-only controller receipt + domain mutation transaction boundary.

No ACK, listener, commits, object writes, provider operation allocation or registry
activation. Caller owns the same fenced transaction and emits ACK only on commit.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.openevolve_event_models import SearchControllerEventReceipt
from backend.app.openevolve_event_projection import (
    inspect_authenticated_controller_event,
)
from backend.app.openevolve_models import SearchControlTransition
from backend.app.openevolve_services import (
    _read_evidence,
    _scope,
    acknowledge_terminal_application,
    register_sampling_intent,
)
from backend.app.services import DomainError
from packages.openevolve_adapter.event_projection import (
    SamplingPayload,
    sampling_command,
    terminal_command,
)
from packages.openevolve_adapter.host_contracts import (
    EvidenceRef,
    SamplingIntentCommand,
    TerminalCommand,
)
from packages.openevolve_adapter.observer_transport import canonical
from packages.research_runtime.broker_contracts import digest


def _validate_projection(parsed, command):
    if isinstance(parsed.payload, SamplingPayload):
        command = SamplingIntentCommand.model_validate(
            command.model_dump(mode="python")
        )
        expected = sampling_command(
            parsed,
            parent_candidate_id=command.intent.parent_candidate_id,
            parent_source_sha256=command.intent.parent_source_sha256,
            mutation_reservation_id=command.intent.mutation_reservation_id,
            selection_ref=command.intent.selection,
            state_ref=command.after_state,
        )
    else:
        command = TerminalCommand.model_validate(command.model_dump(mode="python"))
        expected = terminal_command(
            parsed,
            state_ref=command.state,
            worker_outcome_id=command.worker_outcome_id,
            seed_objective_ack_id=command.seed_objective_ack_id,
        )
    if command != expected:
        raise DomainError(
            "search_event", "Command differs from exact observed event", 409
        )
    return command


def record_controller_event(
    session,
    fence,
    authorization,
    wire,
    *,
    expected_process_profile,
    event_reference: EvidenceRef,
    command,
    settings,
):
    parsed = inspect_authenticated_controller_event(
        session,
        fence,
        authorization,
        wire,
        expected_process_profile=expected_process_profile,
    )
    command = _validate_projection(parsed, command)
    event_reference = EvidenceRef.model_validate(
        event_reference.model_dump(mode="python")
    )
    if event_reference.sha256 != parsed.event_sha256:
        raise DomainError("search_event", "Event object digest differs", 409)
    persisted_event = _read_evidence(
        session, fence, event_reference, "search_controller_event", settings
    )
    if canonical(persisted_event) != canonical(parsed.event.model_dump(mode="json")):
        raise DomainError("search_event", "Event object bytes differ", 409)
    previous = session.scalar(
        select(SearchControllerEventReceipt).where(
            SearchControllerEventReceipt.epoch_id == parsed.event.epoch_id,
            SearchControllerEventReceipt.event_kind == parsed.event.kind,
            SearchControllerEventReceipt.native_iteration == parsed.payload.iteration,
        )
    )
    if previous is not None:
        if (
            any(getattr(previous, key) != value for key, value in _scope(fence).items())
            or previous.admission_id != parsed.event.admission_id
            or previous.event_sha256 != parsed.event_sha256
            or previous.command_sha256 != digest(command)
            or previous.event_object_id != event_reference.id
        ):
            raise DomainError(
                "search_replay", "Conflicting controller semantic replay", 409
            )
        # Peer/fence and immutable evidence were checked above; no latest-frontier,
        # budget mutation or domain method is invoked for historical exact replay.
        return previous
    if isinstance(command, SamplingIntentCommand):
        intent, control = register_sampling_intent(session, fence, command, settings)
        intent_id, terminal_id = intent.id, None
    else:
        terminal = acknowledge_terminal_application(session, fence, command, settings)
        control = session.scalar(
            select(SearchControlTransition).where(
                SearchControlTransition.terminal_id == terminal.id
            )
        )
        intent_id, terminal_id = None, terminal.id
    expected_state = (
        command.after_state
        if isinstance(command, SamplingIntentCommand)
        else command.state
    )
    expected_kind = (
        "sampling"
        if intent_id is not None
        else ("seed" if parsed.payload.iteration == 0 else "terminal")
    )
    if (
        control is None
        or control.epoch_id != parsed.event.epoch_id
        or any(getattr(control, key) != value for key, value in _scope(fence).items())
        or control.kind != expected_kind
        or control.sampling_intent_id != intent_id
        or control.terminal_id != terminal_id
        or control.state_sha256 != expected_state.sha256
    ):
        raise DomainError(
            "search_event", "Domain mutation lacks exact control receipt", 409
        )
    row = SearchControllerEventReceipt(
        id=uuid4(),
        **_scope(fence),
        epoch_id=parsed.event.epoch_id,
        admission_id=parsed.event.admission_id,
        event_kind=parsed.event.kind,
        native_iteration=parsed.payload.iteration,
        event_sha256=parsed.event_sha256,
        event_object_id=event_reference.id,
        event_object_kind="search_controller_event",
        command_sha256=digest(command),
        control_id=control.id,
        control_sequence=control.sequence,
        control_frontier=control.frontier,
        control_state_sha256=control.state_sha256,
        intent_id=intent_id,
        terminal_id=terminal_id,
    )
    session.add(row)
    session.flush()
    return row
