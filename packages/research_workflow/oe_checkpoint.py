"""Actual closed OE stage evidence references, never release or resume authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256


class OEExecutionReference(Contract):
    execution_id: UUID
    candidate_id: UUID
    program_version_id: UUID
    result_sha256: Sha256
    measurement_sha256: Sha256


class OEStageCheckpoint(Contract):
    schema_version: Literal["mineagent.workflow-oe-checkpoint.v1"] = (
        "mineagent.workflow-oe-checkpoint.v1"
    )
    owner_id: UUID
    workflow_id: UUID
    stage_id: UUID
    input_revision_id: UUID
    input_sha256: Sha256
    definition_sha256: Sha256
    allocation_sha256: Sha256
    workflow_submission_id: UUID
    child_submission_id: UUID
    child_run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, ge=1)
    epoch_id: UUID
    controller_admission_id: UUID
    controller_stop_sha256: Sha256
    process_admission_ids: tuple[UUID, ...] = Field(min_length=1, max_length=4)
    provider_transport_ids: tuple[UUID, ...] = Field(min_length=1, max_length=2)
    provider_receipts_sha256: Sha256
    reservation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=256)
    executions: tuple[OEExecutionReference, ...] = Field(min_length=1, max_length=3)
    terminal_application_ids: tuple[UUID, ...] = Field(min_length=2, max_length=3)
    terminal_applications_sha256: Sha256
    model_consumption_sha256: Sha256
    qualification: Literal["closed_owned_search_not_output_release_or_causal_gain"] = (
        "closed_owned_search_not_output_release_or_causal_gain"
    )

    @model_validator(mode="after")
    def distinct(self):
        for values in (
            self.process_admission_ids,
            self.provider_transport_ids,
            self.reservation_ids,
            self.terminal_application_ids,
            tuple(x.execution_id for x in self.executions),
            tuple(x.candidate_id for x in self.executions),
        ):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate actual OE boundary reference")
        if self.controller_admission_id not in self.process_admission_ids:
            raise ValueError("Actual controller missing from stopped process inventory")
        return self


def provider_receipt_commitment(members, receipts):
    """Stable identity/hash projection; caller proves actual complete inventory."""
    from packages.factors.contracts import digest

    def project(rows, fields):
        result = []
        for row in rows:
            result.append(
                {
                    key: str(row[key]) if isinstance(row[key], UUID) else row[key]
                    for key in fields
                }
            )
        if len({row["id"] for row in result}) != len(result):
            raise ValueError("Duplicate physical evidence row")
        return sorted(result, key=lambda row: row["id"])

    return digest(
        {
            "members": project(
                members,
                (
                    "id",
                    "iteration_intent_id",
                    "transport_id",
                    "operation_id",
                    "request_sha256",
                    "model_binding_sha256",
                ),
            ),
            "receipts": project(
                receipts,
                (
                    "id",
                    "membership_id",
                    "kind",
                    "receipt_object_id",
                    "receipt_object_kind",
                    "receipt_sha256",
                    "size_bytes",
                ),
            ),
        }
    )


def model_consumption_commitment(rows):
    """References to actual requests, not a claimed consumed boolean or timestamp."""
    from packages.factors.contracts import digest

    fields = (
        "id",
        "owner_id",
        "workflow_id",
        "stage_id",
        "input_revision_id",
        "input_sha256",
        "run_id",
        "attempt_id",
        "generation",
        "operation_id",
        "transport_id",
        "request_sha256",
    )
    result = [
        {
            key: str(row[key]) if isinstance(row[key], UUID) else row[key]
            for key in fields
        }
        for row in rows
    ]
    if len({row["id"] for row in result}) != len(result):
        raise ValueError("Duplicate actual model consumption")
    return digest(sorted(result, key=lambda row: row["id"]))


def terminal_application_commitment(rows):
    """Complete actual application identities and canonical-event commitments."""
    from packages.factors.contracts import digest

    fields = (
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "epoch_id",
        "ordinal",
        "control_state_id",
        "worker_outcome_id",
        "outcome",
        "event_sha256",
    )
    result = [
        {
            key: str(row[key]) if isinstance(row[key], UUID) else row[key]
            for key in fields
        }
        for row in rows
    ]
    if len({row["id"] for row in result}) != len(result) or len(
        {row["ordinal"] for row in result}
    ) != len(result):
        raise ValueError("Duplicate actual terminal application")
    return digest(sorted(result, key=lambda row: row["ordinal"]))
