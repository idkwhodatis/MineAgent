"""027 structured search wire data; no table registration or launch capability."""

import hashlib
import math
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import canonical_bytes

from .candidate import (
    CandidateDocument,
    Closed,
    TypedFrozenCandidateDomain,
    _raw_fields,
)
from .objective import StructuredObjective

SHA = Annotated[str, Field(pattern="^[a-f0-9]{64}$")]


class StructuredSearchSpec(Closed):
    schema_version: Literal["mineagent.oe-structured-search.v1"] = (
        "mineagent.oe-structured-search.v1"
    )
    owner_id: UUID
    run_id: UUID
    execution_binding_id: UUID
    execution_binding_sha256: SHA
    domain: TypedFrozenCandidateDomain
    seed_candidate_sha256: SHA
    objective: StructuredObjective
    maximum_mutation_slots: int = Field(ge=1, le=2)
    rng_seed: Literal[0] = 0
    population_size: Literal[8] = 8
    archive_size: Literal[8] = 8
    input_policy: Literal["fixed_completed_snapshot_market_no_labels"] = (
        "fixed_completed_snapshot_market_no_labels"
    )

    @model_validator(mode="after")
    def exact(self):
        domain = TypedFrozenCandidateDomain.model_validate(_raw_fields(self.domain))
        objective = StructuredObjective.model_validate(_raw_fields(self.objective))
        if (
            hashlib.sha256(
                canonical_bytes(CandidateDocument(program=domain.seed_program))
            ).hexdigest()
            != self.seed_candidate_sha256
        ):
            raise ValueError("Seed document differs from frozen domain")
        signal, window = domain.seed_program.signal_window, objective.window
        if (
            not (window.start <= signal.start < signal.end < window.end)
            or (window.end - window.start).days > 8
        ):
            raise ValueError(
                "Fixed window must contain signal and later execution session"
            )
        if any(
            type(v) is not int
            for v in (self.rng_seed, self.population_size, self.archive_size)
        ):
            raise ValueError("Exact fixed runtime integers required")
        return self


class StructuredBootstrap(Closed):
    schema_version: Literal["mineagent.oe-structured-bootstrap.v1"] = (
        "mineagent.oe-structured-bootstrap.v1"
    )
    admission_id: UUID
    epoch_id: UUID
    spec: StructuredSearchSpec
    issued_at: datetime
    not_after: datetime
    physical_profile_sha256: SHA
    runtime_profile_sha256: SHA
    config_sha256: SHA
    adapter_patch_sha256: SHA

    @model_validator(mode="after")
    def bounded(self):
        if (
            any(
                t.tzinfo is None or t.utcoffset().total_seconds() != 0
                for t in (self.issued_at, self.not_after)
            )
            or not 0 < (self.not_after - self.issued_at).total_seconds() <= 600
        ):
            raise ValueError("Exact bounded UTC controller deadline required")
        return self

    def remaining(self, now):
        value = self.not_after.timestamp() - now
        if (
            not self.issued_at.timestamp() <= now < self.not_after.timestamp()
            or not math.isfinite(value)
            or not 0 < value <= 600
        ):
            raise ValueError("Original structured controller deadline expired")
        return value


class SearchOperationContext(Closed):
    schema_version: Literal["mineagent.oe-structured-context.v1"] = (
        "mineagent.oe-structured-context.v1"
    )
    owner_id: UUID
    run_id: UUID
    epoch_id: UUID
    iteration_intent_id: UUID
    native_iteration: int = Field(ge=1, le=2)
    purpose: Literal["structured_mutation"] = "structured_mutation"
    operation_ordinal: Literal[0] = 0
    parent_candidate_id: UUID
    parent_candidate_sha256: SHA
    execution_binding_sha256: SHA
    selection_snapshot_sha256: SHA
    rendered_context_sha256: SHA

    @model_validator(mode="after")
    def exact_ordinal(self):
        if type(self.operation_ordinal) is not int:
            raise ValueError("Exact operation ordinal required")
        return self


class EvaluationGrant(Closed):
    schema_version: Literal["mineagent.oe-structured-evaluation-grant.v1"] = (
        "mineagent.oe-structured-evaluation-grant.v1"
    )
    owner_id: UUID
    run_id: UUID
    epoch_id: UUID
    candidate_id: UUID
    upstream_program_id: UUID
    candidate_sha256: SHA
    program_sha256: SHA
    execution_id: UUID
    execution_binding_sha256: SHA
    ticket: SHA
    phase: Literal["seed", "mutation"]


class EvaluationResponse(Closed):
    schema_version: Literal["mineagent.oe-structured-evaluation-response.v1"] = (
        "mineagent.oe-structured-evaluation-response.v1"
    )
    candidate_id: UUID
    candidate_sha256: SHA
    program_sha256: SHA
    execution_id: UUID
    execution_binding_sha256: SHA
    result_sha256: SHA
    objective_sha256: SHA
    objective_receipt_id: UUID
    eligible: bool
    combined_score: float | None
    reason: Literal["eligible", "undefined_accounting", "minimum_closed_trades"]
    feedback: str = Field(max_length=16384)

    @model_validator(mode="after")
    def exact_score(self):
        if (
            self.eligible != (self.reason == "eligible")
            or (
                self.eligible
                and (
                    self.combined_score is None
                    or not math.isfinite(self.combined_score)
                )
            )
            or (not self.eligible and self.combined_score is not None)
        ):
            raise ValueError("Exact eligible score or ineligible null required")
        if len(self.feedback.encode()) > 16384 or "\0" in self.feedback:
            raise ValueError("Bounded feedback required")
        return self
