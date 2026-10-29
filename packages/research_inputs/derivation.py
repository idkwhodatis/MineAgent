"""Immutable deterministic stage inputs and honest append-only attempt receipts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, canonical_bytes
from packages.research_inputs.contracts import ExternalFeedMapping, InputVersionRef


class ExternalNormalizationDefinition(Contract):
    schema_version: Literal["mineagent.external-normalization-stage.v1"] = (
        "mineagent.external-normalization-stage.v1"
    )
    raw_object_id: UUID
    raw_sha256: Sha256
    mapping: ExternalFeedMapping

    @model_validator(mode="after")
    def retained_definition_bound(self):
        if (
            len(canonical_bytes(self.mapping)) > 4096
            or len(canonical_bytes(self)) > 8192
        ):
            raise ValueError("Retained normalization definition exceeds byte bound")
        return self


class DerivationEvent(Contract):
    id: UUID
    derivation_id: UUID
    ordinal: int = Field(strict=True, ge=1, le=3)
    state: Literal[
        "prepared",
        "started",
        "succeeded",
        "rejected",
        "failed",
        "cancelled",
        "interrupted",
    ]
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    outputs: tuple[InputVersionRef, ...] = Field(default=(), max_length=32)
    recorded_at: datetime

    @model_validator(mode="after")
    def consistent(self):
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("Timezone-aware receipt required")
        if self.state in {"prepared", "started", "succeeded"}:
            if self.error_code is not None:
                raise ValueError("Successful/nonterminal receipt has no error")
        elif self.error_code is None:
            raise ValueError("Terminal error code required")
        if (self.state == "succeeded") != bool(self.outputs):
            raise ValueError("Only success contains actual output references")
        if len({(v.origin, v.record_id, v.version_id) for v in self.outputs}) != len(
            self.outputs
        ):
            raise ValueError("Duplicate output reference")
        if any(
            v.origin
            not in {"normalized_external_proposal", "experiment_feedback_proposal"}
            for v in self.outputs
        ):
            raise ValueError("Derivation cannot manufacture manual/unknown inputs")
        return self


def validate_event_prefix(events):
    """No inference from wall-clock age; started-only is not a live-worker proof."""
    events = tuple(DerivationEvent.model_validate(value) for value in events)
    if not events or len(events) > 3 or events[0].state != "prepared":
        raise ValueError("Prepared receipt prefix required")
    if len({event.id for event in events}) != len(events):
        raise ValueError("Duplicate event identity")
    for index, event in enumerate(events):
        if event.ordinal != index + 1 or event.derivation_id != events[0].derivation_id:
            raise ValueError("Exact derivation ordinal required")
        if index and event.recorded_at < events[index - 1].recorded_at:
            raise ValueError("Receipt timestamps move backwards")
    if len(events) >= 2 and events[1].state not in {"started", "cancelled"}:
        raise ValueError("Only start or early cancellation follows preparation")
    if (
        len(events) == 2
        and events[1].state == "cancelled"
        and events[1].error_code != "owner_cancelled"
    ):
        raise ValueError("Early cancellation requires exact owner cancellation")
    if len(events) == 3 and (
        events[1].state != "started"
        or events[2].state
        not in {"succeeded", "rejected", "failed", "cancelled", "interrupted"}
    ):
        raise ValueError("Only a terminal receipt follows actual start")
    return (
        "started_no_terminal_receipt"
        if events[-1].state == "started"
        else events[-1].state
    )
