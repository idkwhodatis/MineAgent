"""Internal stage claim command: IDs and CAS, never server runtime knobs."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from packages.factors.contracts import Contract, Sha256


class WorkflowRDClaim(Contract):
    schema_version: Literal["mineagent.workflow-rd-claim.v1"] = (
        "mineagent.workflow-rd-claim.v1"
    )
    workflow_id: UUID
    expected_revision: int = Field(strict=True, ge=1, lt=1000000)
    input_revision_id: UUID
    idempotency_key: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


class WorkflowRDClaimReceipt(Contract):
    schema_version: Literal["mineagent.workflow-rd-claim-receipt.v1"] = (
        "mineagent.workflow-rd-claim-receipt.v1"
    )
    workflow_id: UUID
    command_id: UUID
    command_sha256: Sha256
    stage_id: UUID
    child_run_id: UUID
    submission_id: UUID
    revision: int = Field(strict=True, ge=2, le=1000000)
    qualification: Literal["queued_child_not_model_consumption_or_completed_output"] = (
        "queued_child_not_model_consumption_or_completed_output"
    )
