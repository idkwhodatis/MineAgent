"""Strict normal027 event data grammar; no socket, owner or execution authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .candidate import Closed
from .contracts import SHA


class SamplingObservation(Closed):
    schema_version: Literal["mineagent.oe-structured-sampling-transition.v1"]
    before_state_sha256: SHA
    after_state: dict


class SamplingPayload(Closed):
    iteration: int = Field(ge=1, le=2)
    parent_id: UUID
    inspiration_ids: tuple[UUID, ...] = Field(max_length=3)
    snapshot: dict
    sampling: SamplingObservation

    @model_validator(mode="after")
    def unique_inspirations(self):
        if len(set(self.inspiration_ids)) != len(self.inspiration_ids):
            raise ValueError("Duplicate inspiration ID")
        return self


class WorkerPlacementObservation(Closed):
    schema_version: Literal["mineagent.oe-structured-worker-placement.v1"]
    epoch_id: UUID
    intent_id: UUID
    controller_namespace_pid: int = Field(ge=1, le=2147483647)
    worker_namespace_pid: int = Field(ge=1, le=2147483647)
    worker_start_ticks: int = Field(ge=1, le=9223372036854775807)

    @model_validator(mode="after")
    def distinct_processes(self):
        if self.controller_namespace_pid == self.worker_namespace_pid:
            raise ValueError("Scheduled worker is not the controller")
        return self


class WorkerDescriptor(Closed):
    schema_version: Literal["mineagent.oe-structured-iteration-grant.v1"]
    admission_id: UUID
    epoch_id: UUID
    intent_id: UUID
    intent_ticket: SHA
    native_iteration: int = Field(ge=1, le=2)
    parent_candidate_id: UUID
    parent_candidate_sha256: SHA
    selection_sha256: SHA
