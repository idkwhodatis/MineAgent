"""Closed host-owned physical evaluation grant and objective response."""

import math
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.research_runtime.broker_contracts import Contract

from .contracts import SHA


class EvaluationGrant(Contract):
    schema_version: Literal["mineagent.search-evaluation-grant.v1"] = (
        "mineagent.search-evaluation-grant.v1"
    )
    owner_id: UUID
    run_id: UUID
    epoch_id: UUID
    candidate_id: UUID
    upstream_program_id: UUID
    source_sha256: SHA
    ticket: SHA
    phase: Literal["seed", "mutation"]


class EvaluationResponse(Contract):
    schema_version: Literal["mineagent.search-evaluation-response.v1"] = (
        "mineagent.search-evaluation-response.v1"
    )
    candidate_id: UUID
    source_sha256: SHA
    objective_sha256: SHA
    objective_receipt_id: UUID
    eligible: bool
    combined_score: float | None
    reason: Literal["eligible", "native_undefined", "native_error", "minimum_trades"]
    feedback: str = Field(max_length=16384)

    @model_validator(mode="after")
    def closed_score(self):
        if self.eligible:
            if (
                self.reason != "eligible"
                or self.combined_score is None
                or not math.isfinite(self.combined_score)
            ):
                raise ValueError("Eligible objective requires finite exact score")
        elif self.reason == "eligible" or self.combined_score is not None:
            raise ValueError("Ineligible objective cannot carry synthetic score")
        if len(self.feedback.encode()) > 16384 or "\x00" in self.feedback:
            raise ValueError("Objective feedback exceeds bounded text profile")
        return self
