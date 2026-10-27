"""Controller-attested namespace identity, not worker authentication or issuance."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import Contract


class WorkerPlacementObservation(Contract):
    schema_version: Literal["mineagent.openevolve-worker-placement.v1"]
    epoch_id: UUID
    intent_id: UUID
    controller_namespace_pid: int = Field(ge=1, le=2147483647)
    worker_namespace_pid: int = Field(ge=1, le=2147483647)
    worker_start_ticks: int = Field(ge=1, le=9223372036854775807)

    @model_validator(mode="after")
    def distinct_child(self):
        if self.controller_namespace_pid == self.worker_namespace_pid:
            raise ValueError("Expected worker cannot be controller leader")
        return self


class WorkerPlacementEvent(Contract):
    schema_version: Literal["mineagent.openevolve-placement-event.v1"]
    admission_id: UUID
    placement: WorkerPlacementObservation


def parse_placement_wire(raw):
    """Closed data parser only; the socket peer must be authenticated separately."""
    import hashlib
    import json

    from pydantic import TypeAdapter

    from .contracts import SHA
    from .observer_transport import canonical, unique

    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError("Placement message byte bound")
    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )
    if type(value) is not dict or set(value) != {
        "event",
        "event_sha256",
        "process_ticket",
    }:
        raise ValueError("Closed placement envelope required")
    for key in ("event_sha256", "process_ticket"):
        TypeAdapter(SHA).validate_python(value[key], strict=True)
    encoded = canonical(value["event"])
    if hashlib.sha256(encoded).hexdigest() != value["event_sha256"]:
        raise ValueError("Placement event hash differs")
    event = WorkerPlacementEvent.model_validate_json(encoded)
    if canonical(event.model_dump(mode="json")) != encoded:
        raise ValueError("Noncanonical placement identity")
    # Ticket is returned separately and must never enter stored evidence/logs.
    return event, value["event_sha256"], value["process_ticket"]
