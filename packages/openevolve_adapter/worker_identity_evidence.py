"""Closed host observation evidence; values never stand in for live peer checks."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import SHA, Contract
from .placement_contracts import WorkerPlacementObservation


class KernelIdentity(Contract):
    host_pid: int = Field(ge=1, le=2147483647)
    start_ticks: int = Field(ge=1, le=9223372036854775807)
    pid_namespace: str = Field(pattern=r"^pid:\[[0-9]+\]$", max_length=128)
    cgroup: str = Field(min_length=1, max_length=4096)


class ObservedWorker(KernelIdentity):
    namespace_pid: int = Field(ge=1, le=2147483647)
    parent_host_pid: int = Field(ge=1, le=2147483647)


class WorkerIdentityEvidence(Contract):
    schema_version: Literal["mineagent.openevolve-worker-identity.v1"]
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1)
    admission_id: UUID
    epoch_id: UUID
    intent_id: UUID
    ticket_id: UUID
    placement_event_sha256: SHA
    placement: WorkerPlacementObservation
    controller: KernelIdentity
    worker: ObservedWorker

    @model_validator(mode="after")
    def exact_observed_tuple(self):
        if (
            self.placement.epoch_id != self.epoch_id
            or self.placement.intent_id != self.intent_id
            or self.worker.namespace_pid != self.placement.worker_namespace_pid
            or self.worker.start_ticks != self.placement.worker_start_ticks
            or self.worker.parent_host_pid != self.controller.host_pid
            or self.worker.host_pid == self.controller.host_pid
            or self.worker.pid_namespace != self.controller.pid_namespace
            or self.worker.cgroup != self.controller.cgroup
        ):
            raise ValueError("Closed worker identity lineage differs")
        return self
