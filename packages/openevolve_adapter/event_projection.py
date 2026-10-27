"""Closed controller-event parsing/projection; never peer or database authority.

No socket listener, registry registration, object publication or ACK is exposed.
Host-resolved identities remain subject to the neutral transaction, current peer
purpose binding, scoped evidence rows and committed service transaction.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from .checkpoint_native import _normalize, state_sha256
from .contracts import SHA, Contract
from .host_contracts import (
    EvidenceRef,
    IterationIntentCommand,
    SamplingIntentCommand,
    TerminalCommand,
)
from .observer_transport import canonical, unique

MAX_WIRE_BYTES = 1024 * 1024


class SamplingObservation(Contract):
    schema_version: Literal["mineagent.native-sampling-transition.v1"]
    before_state_sha256: SHA
    after_state: dict


class SamplingPayload(Contract):
    iteration: int = Field(ge=1, le=2)
    parent_id: UUID
    inspiration_ids: tuple[UUID, ...] = Field(max_length=8)
    snapshot: dict
    sampling: SamplingObservation

    @model_validator(mode="after")
    def distinct_references(self):
        if len(set(self.inspiration_ids)) != len(self.inspiration_ids):
            raise ValueError("Duplicate observed inspiration")
        return self


class TerminalPayload(Contract):
    iteration: int = Field(ge=0, le=2)
    outcome: Literal["seed", "accepted", "rejected", "error"]
    native_state: dict
    state_sha256: SHA

    @model_validator(mode="after")
    def seed_phase(self):
        if (self.iteration == 0) != (self.outcome == "seed"):
            raise ValueError("Terminal phase differs")
        return self


class ControllerEvent(Contract):
    schema_version: Literal["mineagent.openevolve-event.v1"]
    admission_id: UUID
    epoch_id: UUID
    kind: Literal["iteration_intent", "terminal_applied"]
    payload: dict


@dataclass(frozen=True)
class ParsedControllerEvent:
    """Untrusted parsed content, explicitly NOT an authorization capability."""

    event: ControllerEvent
    payload: SamplingPayload | TerminalPayload
    event_sha256: str
    process_ticket: str = field(repr=False)


@dataclass(frozen=True)
class EvidenceProjection:
    kind: Literal["search_context", "search_population"]
    content: bytes

    @property
    def sha256(self):
        return hashlib.sha256(self.content).hexdigest()


def _state(value):
    if type(value) is not dict or set(value) != {"metadata", "programs"}:
        raise ValueError("Closed native state required")
    return _normalize(value["metadata"], value["programs"])


def parse_controller_event(wire: bytes) -> ParsedControllerEvent:
    if type(wire) is not bytes or not 0 < len(wire) <= MAX_WIRE_BYTES:
        raise ValueError("Controller event byte bound")
    value = json.loads(
        wire.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )
    if type(value) is not dict or set(value) != {
        "event",
        "event_sha256",
        "process_ticket",
    }:
        raise ValueError("Closed controller envelope required")
    for key in ("event_sha256", "process_ticket"):
        TypeAdapter(SHA).validate_python(value[key], strict=True)
    encoded = canonical(value["event"])
    if hashlib.sha256(encoded).hexdigest() != value["event_sha256"]:
        raise ValueError("Controller event digest differs")
    event = ControllerEvent.model_validate_json(encoded)
    if canonical(event.model_dump(mode="json")) != encoded:
        raise ValueError("Noncanonical controller identity")
    payload_type = (
        SamplingPayload if event.kind == "iteration_intent" else TerminalPayload
    )
    payload = payload_type.model_validate_json(canonical(event.payload))
    if canonical(payload.model_dump(mode="json")) != canonical(event.payload):
        raise ValueError("Noncanonical observed payload identity")
    state = _state(
        payload.sampling.after_state
        if isinstance(payload, SamplingPayload)
        else payload.native_state
    )
    if isinstance(payload, TerminalPayload):
        if (
            state_sha256(state) != payload.state_sha256
            or state["metadata"]["last_iteration"] != payload.iteration
        ):
            raise ValueError("Terminal state/frontier digest differs")
    else:
        if str(payload.parent_id) not in state["programs"]:
            raise ValueError("Observed selected parent absent")
        snapshot = payload.snapshot
        if (
            set(snapshot)
            != {
                "programs",
                "islands",
                "current_island",
                "feature_dimensions",
                "artifacts",
                "sampling_island",
            }
            or type(snapshot["current_island"]) is not int
            or snapshot["current_island"] != 0
            or type(snapshot["sampling_island"]) is not int
            or snapshot["sampling_island"] != 0
            or snapshot["feature_dimensions"] != ["complexity", "diversity"]
            or canonical(snapshot["programs"]) != canonical(state["programs"])
            or type(snapshot["islands"]) is not list
            or len(snapshot["islands"]) != 1
            or type(snapshot["islands"][0]) is not list
            or sorted(snapshot["islands"][0]) != state["metadata"]["islands"][0]
            or type(snapshot["artifacts"]) is not dict
        ):
            raise ValueError("Closed actual worker snapshot required")
    return ParsedControllerEvent(
        event, payload, value["event_sha256"], value["process_ticket"]
    )


def evidence_projections(
    parsed: ParsedControllerEvent,
) -> tuple[EvidenceProjection, ...]:
    payload = parsed.payload
    if isinstance(payload, TerminalPayload):
        return (
            EvidenceProjection(
                "search_population", canonical(_state(payload.native_state))
            ),
        )
    selection = {
        "schema_version": "mineagent.native-selection.v1",
        "native_iteration": payload.iteration,
        "parent_id": str(payload.parent_id),
        "inspiration_ids": [str(x) for x in payload.inspiration_ids],
        "snapshot": payload.snapshot,
    }
    return (
        EvidenceProjection("search_context", canonical(selection)),
        EvidenceProjection(
            "search_population", canonical(_state(payload.sampling.after_state))
        ),
    )


def _exact_evidence(reference: EvidenceRef, projection: EvidenceProjection):
    reference = EvidenceRef.model_validate(reference.model_dump(mode="python"))
    if reference.sha256 != projection.sha256:
        raise ValueError("Registered evidence differs from observed projection")
    return reference


def sampling_command(
    parsed,
    *,
    parent_candidate_id,
    parent_source_sha256,
    mutation_reservation_id,
    selection_ref,
    state_ref,
):
    if not isinstance(parsed.payload, SamplingPayload):
        raise TypeError("Sampling event required")
    selection, state = evidence_projections(parsed)
    return SamplingIntentCommand(
        intent=IterationIntentCommand(
            epoch_id=parsed.event.epoch_id,
            native_iteration=parsed.payload.iteration,
            parent_candidate_id=parent_candidate_id,
            parent_source_sha256=parent_source_sha256,
            mutation_reservation_id=mutation_reservation_id,
            selection=_exact_evidence(selection_ref, selection),
        ),
        before_state_sha256=parsed.payload.sampling.before_state_sha256,
        after_state=_exact_evidence(state_ref, state),
    )


def terminal_command(
    parsed, *, state_ref, worker_outcome_id=None, seed_objective_ack_id=None
):
    if not isinstance(parsed.payload, TerminalPayload):
        raise TypeError("Terminal event required")
    return TerminalCommand(
        epoch_id=parsed.event.epoch_id,
        frontier=parsed.payload.iteration,
        outcome=parsed.payload.outcome,
        state=_exact_evidence(state_ref, evidence_projections(parsed)[0]),
        worker_outcome_id=worker_outcome_id,
        seed_objective_ack_id=seed_objective_ack_id,
    )
