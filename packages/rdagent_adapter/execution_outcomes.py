"""Bounded 021 execution evidence documents, not execution/publication authority.

The host must independently establish every referenced object, scope, process,
measurement and actual upstream decision. These codecs neither verify those
facts nor register a provider, prove source PIT, or approve an adoption.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import (
    Contract,
    DateWindow,
    Sha256,
    canonical_bytes,
    digest,
)

MAX_DOCUMENT_BYTES = 65536


class DeclaredWindow(DateWindow):
    complete: Literal[True]

    @field_validator("complete", mode="before")
    @classmethod
    def exact_complete(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Complete window requires literal true")
        return value


class ExecutionEvidence(Contract):
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    start_id: UUID
    source_id: UUID
    evaluation_id: UUID
    snapshot_id: UUID
    factor_version_id: UUID
    code_sha256: Sha256
    input_sha256: Sha256
    output_sha256: Sha256
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def bounded_execution_document(self):
        if len(canonical_bytes(self)) > MAX_DOCUMENT_BYTES:
            raise ValueError("Execution document exceeds 65536 canonical bytes")
        return self

    @property
    def sha256(self):
        return digest(self)


class RDExecutionProposal(ExecutionEvidence):
    schema_version: Literal["mineagent.rd-execution-proposal.v1"] = (
        "mineagent.rd-execution-proposal.v1"
    )
    source_assembly_id: UUID
    source_assembly_sha256: Sha256


class RDExecutionFeedback(ExecutionEvidence):
    schema_version: Literal["mineagent.rd-execution-feedback.v1"] = (
        "mineagent.rd-execution-feedback.v1"
    )
    upstream_feedback: str = Field(
        strict=True, min_length=1, max_length=32768, pattern=r"^[^\x00]+$"
    )
    row_count: int = Field(strict=True, ge=1, le=1000)
    missing_count: int = Field(strict=True, ge=0, le=1000)
    declared_window: DeclaredWindow
    objective_protocol: Literal["maintained_fixture_exact_rows.v1"] = (
        "maintained_fixture_exact_rows.v1"
    )
    objective_passed: bool = Field(strict=True)

    @model_validator(mode="after")
    def missing_within_rows(self):
        if self.missing_count > self.row_count:
            raise ValueError("Missing count exceeds measured rows")
        return self


class RDExecutionAdoption(ExecutionEvidence):
    schema_version: Literal["mineagent.rd-execution-adoption.v1"] = (
        "mineagent.rd-execution-adoption.v1"
    )
    observation_id: UUID
    boundary_id: UUID
    controller_launch_id: UUID
    proposal_object_id: UUID
    proposal_sha256: Sha256
    feedback_object_id: UUID
    feedback_sha256: Sha256
    adopted_source_id: UUID
    decision: Literal["accepted"] = "accepted"
    logical_step: Literal[0] = 0

    @field_validator("logical_step", mode="before")
    @classmethod
    def exact_step(cls, value):
        if type(value) is not int or value != 0:
            raise ValueError("Initial adoption requires exact integer step zero")
        return value

    @model_validator(mode="after")
    def adopted_current_source(self):
        if self.adopted_source_id != self.source_id:
            raise ValueError("Initial accepted adoption must select its actual source")
        return self


DOCUMENT_MODELS = {
    "factor_generation_proposal": RDExecutionProposal,
    "factor_generation_feedback": RDExecutionFeedback,
    "factor_adoption_receipt": RDExecutionAdoption,
}


def canonical_document(model) -> bytes:
    """Revalidate exactly one supported document; no authority is conferred."""
    if type(model) not in DOCUMENT_MODELS.values():
        raise ValueError("Unsupported execution document type")
    checked = type(model).model_validate(model)
    return canonical_bytes(checked)


def parse_document(kind: str, raw: bytes):
    """Accept only bounded canonical bytes with explicit fields and no duplicates."""
    import json

    if type(kind) is not str or kind not in DOCUMENT_MODELS:
        raise ValueError("Unsupported execution document kind")
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
        raise ValueError("Execution document must be bounded bytes")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate execution document key")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("Nonfinite JSON is not an execution document")

    try:
        json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
        )
        model = DOCUMENT_MODELS[kind].model_validate_json(raw)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid execution document encoding or depth") from exc
    if canonical_document(model) != raw:
        raise ValueError("Execution document bytes are not canonical")
    return model
