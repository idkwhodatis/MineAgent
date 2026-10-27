"""Closed initial-epoch host commands, not public/client launch authority."""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.research_runtime.broker_contracts import Contract

from .contracts import SHA
from .runtime_profile import validate_source_bound


class EvidenceRef(Contract):
    id: UUID
    sha256: SHA


class SourceEnvelope(Contract):
    schema_version: Literal["mineagent.search-source.v1"] = "mineagent.search-source.v1"
    source: str
    source_sha256: SHA

    @model_validator(mode="after")
    def exact_bytes(self):
        raw = self.source.encode()
        validate_source_bound(raw)
        if hashlib.sha256(raw).hexdigest() != self.source_sha256:
            raise ValueError("Source envelope and bytes disagree")
        return self


class CandidateIntentCommand(Contract):
    epoch_id: UUID
    admission_id: UUID
    upstream_program_id: UUID
    phase: Literal["seed", "mutation"]
    intent_id: UUID | None = None
    source: EvidenceRef
    ticket_sha256: SHA
    request_sha256: SHA

    @model_validator(mode="after")
    def phase_identity(self):
        if (self.phase == "seed") != (self.intent_id is None):
            raise ValueError("Seed/mutation intent mismatch")
        return self


class ObjectiveAckCommand(Contract):
    epoch_id: UUID
    grant_id: UUID
    objective_receipt_id: UUID
    eligible: bool
    combined_score: float | None
    request_sha256: SHA


class WorkerOutcomeCommand(Contract):
    epoch_id: UUID
    intent_id: UUID
    native_iteration: int = Field(ge=1, le=2)
    status: Literal["evaluated", "worker_error"]
    objective_ack_id: UUID | None = None
    evidence: EvidenceRef

    @model_validator(mode="after")
    def evaluated_needs_receipt(self):
        if self.status == "evaluated" and self.objective_ack_id is None:
            raise ValueError("Evaluated worker requires exact objective receipt")
        return self


class TerminalCommand(Contract):
    epoch_id: UUID
    frontier: int = Field(ge=0, le=2)
    outcome: Literal["seed", "accepted", "rejected", "error"]
    worker_outcome_id: UUID | None = None
    seed_objective_ack_id: UUID | None = None
    state: EvidenceRef

    @model_validator(mode="after")
    def seed_boundary(self):
        if self.frontier == 0:
            if (
                self.outcome != "seed"
                or self.seed_objective_ack_id is None
                or self.worker_outcome_id is not None
            ):
                raise ValueError("Seed frontier requires exact seed acknowledgement")
        elif (
            self.outcome == "seed"
            or self.seed_objective_ack_id is not None
            or self.worker_outcome_id is None
        ):
            raise ValueError("Mutation frontier requires exact worker outcome")
        return self


class CheckpointIntentCommand(Contract):
    # None is reserved for historical, unregistered adapter proof fixtures.
    save_ordinal: int | None = Field(default=None, ge=1, le=4)
    epoch_id: UUID
    terminal_id: UUID
    frontier: int = Field(ge=0, le=2)
    state_sha256: SHA
    request_sha256: SHA


class CheckpointFileEnvelope(Contract):
    schema_version: Literal["mineagent.native-checkpoint-file.v1"] = (
        "mineagent.native-checkpoint-file.v1"
    )
    name: str = Field(
        pattern=r"^(metadata\.json|best_program\.py|best_program_info\.json|programs/[0-9a-f-]{36}\.json)$"
    )
    sha256: SHA
    size_bytes: int = Field(ge=1, le=524288)
    data_base64: str = Field(min_length=4, max_length=699052)

    def bytes(self):
        import base64

        raw = base64.b64decode(self.data_base64, validate=True)
        if (
            len(raw) != self.size_bytes
            or hashlib.sha256(raw).hexdigest() != self.sha256
            or base64.b64encode(raw).decode() != self.data_base64
        ):
            raise ValueError("Native checkpoint file bytes differ")
        return raw


class CheckpointFileRef(Contract):
    name: str = Field(
        pattern=r"^(metadata\.json|best_program\.py|best_program_info\.json|programs/[0-9a-f-]{36}\.json)$"
    )
    file_sha256: SHA
    size_bytes: int = Field(ge=1, le=524288)
    object: EvidenceRef


class CheckpointInventory(Contract):
    schema_version: Literal["mineagent.native-checkpoint-inventory.v1"] = (
        "mineagent.native-checkpoint-inventory.v1"
    )
    files: tuple[CheckpointFileRef, ...] = Field(min_length=4, max_length=67)

    @model_validator(mode="after")
    def exact_inventory(self):
        names = [f.name for f in self.files]
        if (
            names != sorted(set(names))
            or sum(f.size_bytes for f in self.files) > 8388608
        ):
            raise ValueError("Checkpoint inventory is not unique ordered and bounded")
        if not {"metadata.json", "best_program.py", "best_program_info.json"} <= set(
            names
        ):
            raise ValueError("Checkpoint inventory lacks native required files")
        for name in names:
            if name.startswith("programs/") and str(UUID(name[9:-5])) != name[9:-5]:
                raise ValueError("Noncanonical native program filename")
        return self


class InitialCheckpointClosure(Contract):
    schema_version: Literal["mineagent.initial-search-checkpoint-closure.v1"] = (
        "mineagent.initial-search-checkpoint-closure.v1"
    )
    epoch_id: UUID
    frontier: int = Field(ge=0, le=2)
    spec_sha256: SHA
    state_sha256: SHA
    control_sequence: int = Field(ge=1, le=5)
    control_transition_ids: tuple[UUID, ...] = Field(min_length=1, max_length=5)
    sampling_transition_ids: tuple[UUID, ...] = Field(max_length=2)
    controller_event_receipt_ids: tuple[UUID, ...] = Field(min_length=1, max_length=5)
    worker_ticket_ids: tuple[UUID, ...] = Field(max_length=2)
    worker_expected_identity_ids: tuple[UUID, ...] = Field(max_length=2)
    worker_assignment_ids: tuple[UUID, ...] = Field(max_length=2)
    worker_assignment_closure_ids: tuple[UUID, ...] = Field(max_length=2)
    open_worker_assignment_ids: tuple[UUID, ...] = Field(max_length=1)
    terminal_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    candidate_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    objective_receipt_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    objective_ack_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    intent_ids: tuple[UUID, ...] = Field(max_length=2)
    worker_outcome_ids: tuple[UUID, ...] = Field(max_length=2)
    pending_intent_ids: tuple[UUID, ...] = Field(max_length=2)
    rendered_context_ids: tuple[UUID, ...] = Field(max_length=2)
    proposal_response_ids: tuple[UUID, ...] = Field(max_length=2)
    proposal_source_ids: tuple[UUID, ...] = Field(max_length=4)
    evaluation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    evaluation_outcome_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    continuation: Literal["not_admitted_initial_epoch_only"] = (
        "not_admitted_initial_epoch_only"
    )

    @model_validator(mode="after")
    def canonical_sets(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, tuple) and list(map(str, value)) != sorted(
                set(map(str, value))
            ):
                raise ValueError("Closure references must be sorted unique identities")
        if (
            len(self.control_transition_ids) != self.control_sequence
            or len(self.controller_event_receipt_ids) != self.control_sequence
            or len(self.worker_ticket_ids) != len(self.intent_ids)
            or len(self.worker_expected_identity_ids) != len(self.intent_ids)
            or not set(self.open_worker_assignment_ids)
            <= set(self.worker_assignment_ids)
            or len(self.worker_assignment_closure_ids) > len(self.worker_assignment_ids)
        ):
            raise ValueError("Checkpoint controller/worker closure cardinality differs")
        if len(self.terminal_ids) != self.frontier + 1 or not set(
            self.pending_intent_ids
        ) <= set(self.intent_ids):
            raise ValueError("Checkpoint closure frontier or pending tail differs")
        return self


class CheckpointPublicationCommand(Contract):
    epoch_id: UUID
    intent_id: UUID
    inventory: EvidenceRef
    closure: EvidenceRef


class PhysicalOutcomeCommand(Contract):
    epoch_id: UUID
    evaluation_id: UUID
    stop_receipt_id: UUID
    status: Literal["observed", "failed"]
    evidence: EvidenceRef
    request_sha256: SHA


class ObjectiveReceiptCommand(Contract):
    epoch_id: UUID
    candidate_id: UUID
    evaluation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=8)
    evidence: EvidenceRef

    @model_validator(mode="after")
    def unique_evaluations(self):
        if len(set(self.evaluation_ids)) != len(self.evaluation_ids):
            raise ValueError("Duplicate physical objective observation")
        return self


class IterationIntentCommand(Contract):
    epoch_id: UUID
    native_iteration: int = Field(ge=1, le=2)
    parent_candidate_id: UUID
    parent_source_sha256: SHA
    mutation_reservation_id: UUID
    selection: EvidenceRef


class PromptReference(Contract):
    role: Literal["previous_summary", "top", "diverse", "inspiration"]
    upstream_program_id: UUID
    source_sha256: SHA
    representation: Literal["summary_without_source_code", "source_code"]


class RenderedProjection(Contract):
    system: str = Field(min_length=1, max_length=262144)
    user: str = Field(min_length=1, max_length=262144)
    references: tuple[PromptReference, ...] = Field(max_length=16)
    current_source_sha256: SHA
    artifact_section_sha256: SHA


class RenderedContextCommand(Contract):
    epoch_id: UUID
    intent_id: UUID
    context_id: UUID
    projection: EvidenceRef


class ProposalResponseCommand(Contract):
    epoch_id: UUID
    intent_id: UUID
    model_outcome_id: UUID
    response_sha256: SHA
    response_text: str = Field(min_length=1, max_length=32768)


class ProposalSourceEnvelope(Contract):
    stage: Literal["applied", "enforced"]
    source: str | None
    source_sha256: SHA
    source_bytes: int = Field(ge=0, le=16777216)
    evidence: Literal["complete_source", "oversize_source_digest"]

    @model_validator(mode="after")
    def exact_proposal(self):
        if self.evidence == "complete_source":
            if self.source is None:
                raise ValueError("Complete proposal requires source")
            raw = self.source.encode()
            # Evidence preserves rejected UTF-8 bytes too (empty, CR, NUL,
            # invalid Python/encoding declarations). Candidate admission applies
            # canonical grammar independently; do not turn ordinary bad output
            # into a lost-evidence or synthetic-score path.
            if len(raw) > 32768:
                raise ValueError("Complete proposal exceeds evidence bound")
            if (
                len(raw) != self.source_bytes
                or hashlib.sha256(raw).hexdigest() != self.source_sha256
            ):
                raise ValueError("Proposal source bytes differ")
        elif self.source is not None or self.source_bytes <= 32768:
            raise ValueError("Oversize proposal cannot claim truncated source")
        return self


class ProposalSourceCommand(Contract):
    epoch_id: UUID
    intent_id: UUID
    proposal_id: UUID
    source: EvidenceRef


def initial_prompt_references(selection):
    """Closed structural check for top=1/diverse=1, not RNG or prompt rerender.

    Pinned worker supplies only its best two programs to the sampler; therefore
    its one diverse choice has exactly one possible member. Preserve actual
    snapshot island order for ties, and actual inspiration order/deduplication.
    Wider profiles must not reuse this checker.
    """
    snapshot = selection["snapshot"]
    programs = snapshot["programs"]
    ids = list(snapshot["islands"][0])
    ids.sort(
        key=lambda identity: programs[identity]["metrics"]["combined_score"],
        reverse=True,
    )
    if not ids:
        raise ValueError("Initial prompt requires nonempty native island")
    selected = ids[:2]
    roles = [("previous_summary", ids[0]), ("top", ids[0])]
    if len(selected) == 2:
        roles.append(("diverse", selected[1]))
    roles.extend(
        ("inspiration", identity)
        for identity in selection["inspiration_ids"]
        if identity not in selected
    )
    return tuple(
        PromptReference(
            role=role,
            upstream_program_id=UUID(identity),
            source_sha256=hashlib.sha256(
                programs[identity]["code"].encode()
            ).hexdigest(),
            representation="summary_without_source_code"
            if role == "previous_summary"
            else "source_code",
        )
        for role, identity in roles
    )


class SamplingIntentCommand(Contract):
    intent: IterationIntentCommand
    before_state_sha256: SHA
    after_state: EvidenceRef
