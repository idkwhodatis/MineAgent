"""One terminal structured stage tied to a real successful RD boundary."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from packages.factors.contracts import Contract, Sha256, digest
from packages.research_workflow.rd_claim import WorkflowRDClaim, WorkflowRDClaimReceipt


class OEStageDefinition(Contract):
    schema_version: Literal["mineagent.workflow-oe-stage.v1"] = (
        "mineagent.workflow-oe-stage.v1"
    )
    plan_sha256: Sha256
    structured_template_sha256: Sha256
    predecessor_checkpoint_id: UUID
    predecessor_checkpoint_sha256: Sha256
    parent_source_stage_id: UUID
    source_run_id: UUID
    source_attempt_id: UUID
    source_generation: int = Field(strict=True, ge=1)
    factor_version_id: UUID
    snapshot_id: UUID
    snapshot_manifest_sha256: Sha256
    analysis_id: UUID
    analysis_result_sha256: Sha256
    release_exposure_id: UUID
    release_provenance_sha256: Sha256

    @property
    def sha256(self):
        return digest(self)


class WorkflowOEClaim(WorkflowRDClaim):
    schema_version: Literal["mineagent.workflow-oe-claim.v1"] = (
        "mineagent.workflow-oe-claim.v1"
    )


class WorkflowOEClaimReceipt(WorkflowRDClaimReceipt):
    schema_version: Literal["mineagent.workflow-oe-claim-receipt.v1"] = (
        "mineagent.workflow-oe-claim-receipt.v1"
    )
