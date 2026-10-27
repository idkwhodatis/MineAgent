"""Closed event/worker evidence reads for initial snapshot publication only."""

import hashlib

from backend.app.services import DomainError
from packages.openevolve_adapter.event_projection import (
    ControllerEvent,
    SamplingPayload,
    TerminalPayload,
)
from packages.openevolve_adapter.host_contracts import (
    EvidenceRef,
    IterationIntentCommand,
    SamplingIntentCommand,
    TerminalCommand,
)
from packages.openevolve_adapter.observer_transport import canonical
from packages.openevolve_adapter.placement_contracts import WorkerPlacementEvent
from packages.openevolve_adapter.worker_identity_evidence import WorkerIdentityEvidence
from packages.research_runtime.broker_contracts import digest


def validate_checkpoint_event_objects(session, fence, epoch, rows, settings):
    from backend.app.openevolve_services import (
        _read_evidence,
        _validated_population,
    )

    return validate_event_objects(
        fence,
        epoch,
        rows,
        read_evidence=lambda reference, kind: _read_evidence(
            session, fence, reference, kind, settings
        ),
        validated_population=lambda row: _validated_population(
            session,
            fence,
            epoch.id,
            row.frontier,
            EvidenceRef(id=row.state_object_id, sha256=row.state_sha256),
            settings,
        ),
    )


def validate_event_objects(fence, epoch, rows, *, read_evidence, validated_population):
    """Pure retained event agreement reused by live and cold consumers.

    Callbacks must independently validate actual registered bytes and complete
    population/objective ancestry. This function never manufactures a live fence.
    """
    controls = {row.id: row for row in rows["control_transition_ids"]}
    intents = {row.id: row for row in rows["intent_ids"]}
    terminals = {row.id: row for row in rows["terminal_ids"]}
    states = {row.id: validated_population(row) for row in controls.values()}
    for receipt in rows["controller_event_receipt_ids"]:
        value = read_evidence(
            EvidenceRef(id=receipt.event_object_id, sha256=receipt.event_sha256),
            "search_controller_event",
        )
        raw = canonical(value)
        event = ControllerEvent.model_validate_json(raw)
        if (
            hashlib.sha256(raw).hexdigest() != receipt.event_sha256
            or canonical(event.model_dump(mode="json")) != raw
            or (event.admission_id, event.epoch_id, event.kind)
            != (epoch.admission_id, epoch.id, receipt.event_kind)
        ):
            raise DomainError(
                "search_checkpoint", "Controller event object identity differs", 409
            )
        control = controls[receipt.control_id]
        if event.kind == "iteration_intent":
            payload = SamplingPayload.model_validate_json(canonical(event.payload))
            intent = intents[receipt.intent_id]
            selection = {
                "schema_version": "mineagent.native-selection.v1",
                "native_iteration": payload.iteration,
                "parent_id": str(payload.parent_id),
                "inspiration_ids": [str(x) for x in payload.inspiration_ids],
                "snapshot": payload.snapshot,
            }
            stored = read_evidence(
                EvidenceRef(
                    id=intent.selection_object_id, sha256=intent.selection_sha256
                ),
                "search_context",
            )
            if (
                canonical(selection) != canonical(stored)
                or hashlib.sha256(canonical(selection)).hexdigest()
                != intent.selection_sha256
                or payload.sampling.before_state_sha256
                != controls[control.previous_id].state_sha256
            ):
                raise DomainError(
                    "search_checkpoint",
                    "Sampling event selection/cursor provenance differs",
                    409,
                )
            projected = payload.sampling.after_state
            command = SamplingIntentCommand(
                intent=IterationIntentCommand(
                    epoch_id=epoch.id,
                    native_iteration=intent.native_iteration,
                    parent_candidate_id=intent.parent_candidate_id,
                    parent_source_sha256=intent.parent_source_sha256,
                    mutation_reservation_id=intent.mutation_reservation_id,
                    selection=EvidenceRef(
                        id=intent.selection_object_id, sha256=intent.selection_sha256
                    ),
                ),
                before_state_sha256=controls[control.previous_id].state_sha256,
                after_state=EvidenceRef(
                    id=control.state_object_id, sha256=control.state_sha256
                ),
            )
        else:
            payload = TerminalPayload.model_validate_json(canonical(event.payload))
            projected = payload.native_state
            terminal = terminals[receipt.terminal_id]
            if (
                payload.outcome != terminal.outcome
                or terminal.frontier != payload.iteration
                or terminal.state_object_id != control.state_object_id
                or terminal.state_sha256 != control.state_sha256
            ):
                raise DomainError(
                    "search_checkpoint", "Terminal event durable semantics differ", 409
                )
            command = TerminalCommand(
                epoch_id=epoch.id,
                frontier=terminal.frontier,
                outcome=terminal.outcome,
                state=EvidenceRef(
                    id=terminal.state_object_id, sha256=terminal.state_sha256
                ),
                worker_outcome_id=terminal.worker_outcome_id,
                seed_objective_ack_id=terminal.seed_objective_ack_id,
            )
            if payload.state_sha256 != control.state_sha256:
                raise DomainError(
                    "search_checkpoint", "Terminal event state hash differs", 409
                )
        if (
            receipt.command_sha256 != digest(command)
            or control.request_sha256 != receipt.command_sha256
        ):
            raise DomainError(
                "search_checkpoint",
                "Controller event domain command digest differs",
                409,
            )
        if payload.iteration != receipt.native_iteration or canonical(
            projected
        ) != canonical(states[control.id]):
            raise DomainError(
                "search_checkpoint",
                "Controller event differs from acknowledged population",
                409,
            )
    for expected in rows["worker_expected_identity_ids"]:
        scope = {
            key: getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
        value = read_evidence(
            EvidenceRef(
                id=expected.evidence_object_id, sha256=expected.evidence_sha256
            ),
            "search_worker_identity",
        )
        evidence = WorkerIdentityEvidence.model_validate_json(canonical(value))
        event = WorkerPlacementEvent(
            schema_version="mineagent.openevolve-placement-event.v1",
            admission_id=epoch.admission_id,
            placement=evidence.placement,
        )
        if (
            hashlib.sha256(canonical(value)).hexdigest() != expected.evidence_sha256
            or canonical(evidence.model_dump(mode="json")) != canonical(value)
            or any(getattr(evidence, key) != value for key, value in scope.items())
            or (
                evidence.epoch_id,
                evidence.admission_id,
                evidence.intent_id,
                evidence.ticket_id,
            )
            != (epoch.id, epoch.admission_id, expected.intent_id, expected.ticket_id)
            or any(
                getattr(evidence.worker, key) != getattr(expected, key)
                for key in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
            )
            or hashlib.sha256(canonical(event.model_dump(mode="json"))).hexdigest()
            != evidence.placement_event_sha256
        ):
            raise DomainError(
                "search_checkpoint", "Worker placement object identity differs", 409
            )
