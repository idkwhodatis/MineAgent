"""Platform JSON continuation only; never pickle or executable native state."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, digest


class CompletedGeneration(Contract):
    logical_step: int = Field(strict=True, ge=0, le=7)
    start_id: UUID
    outcome_id: UUID
    factor_version_id: UUID | None
    evaluated_source_id: UUID | None
    adopted_source_id: UUID | None
    adopted_factor_version_id: UUID | None
    continuation_source_id: UUID | None
    continuation_factor_version_id: UUID | None
    adoption_receipt_sha256: Sha256 | None
    outcome: Literal[
        "malformed_response",
        "source_rejected",
        "execution_failed",
        "valid_unadopted",
        "accepted",
        "cancelled",
    ]

    @model_validator(mode="after")
    def exact_lineage_pairs(self):
        for source, version in (
            (self.evaluated_source_id, self.factor_version_id),
            (self.adopted_source_id, self.adopted_factor_version_id),
            (self.continuation_source_id, self.continuation_factor_version_id),
        ):
            if (source is None) != (version is None):
                raise ValueError("Checkpoint source/version identity must be paired")
        if self.outcome in ("execution_failed", "valid_unadopted", "accepted") and (
            self.evaluated_source_id is None or self.adoption_receipt_sha256 is None
        ):
            raise ValueError("Completed evaluation requires actual adoption evidence")
        if self.outcome == "accepted" and self.adopted_source_id is None:
            raise ValueError("Accepted result must retain actual adopted source")
        return self


class FactorCheckpoint(Contract):
    schema_version: Literal["mineagent.factor-research-checkpoint.v1"] = (
        "mineagent.factor-research-checkpoint.v1"
    )
    config_sha256: Sha256
    input_sha256: Sha256
    completed: tuple[CompletedGeneration, ...] = Field(min_length=1, max_length=8)
    next_step: int = Field(strict=True, ge=1, le=8)
    upstream_state: Literal["rebuild_from_durable_history"] = (
        "rebuild_from_durable_history"
    )
    continuation: Literal["checkpoint_replay_not_lossless_pause"] = (
        "checkpoint_replay_not_lossless_pause"
    )

    @model_validator(mode="after")
    def contiguous(self):
        if [x.logical_step for x in self.completed] != list(range(self.next_step)):
            raise ValueError("Checkpoint must close every preceding logical generation")
        if len({x.start_id for x in self.completed}) != len(self.completed) or len(
            {x.outcome_id for x in self.completed}
        ) != len(self.completed):
            raise ValueError("Checkpoint cannot alias logical generation evidence")
        if any(x.outcome in ("accepted", "cancelled") for x in self.completed[:-1]):
            raise ValueError("Upstream loop cannot continue after terminal feedback")
        return self

    @property
    def sha256(self):
        return digest(self)
