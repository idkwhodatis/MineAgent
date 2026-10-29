"""031 workflow-origin OE data, never an authenticated-user child submission.

The live host still verifies the real predecessor, release, selected inputs and
current operator at submit and claim. A typed context/hash is not that authority.
"""

from typing import Literal

from pydantic import model_validator

from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector

from .candidate import Closed, _raw_fields
from .contracts import SHA, StructuredSearchSpec
from .provider_contracts import OEChatBinding
from .provider_run import (
    FixedExecutionInputs,
    ProviderStructuredRunConfig,
    StructuredRuntimePins,
)
from .workflow_context import WorkflowOEContext


class WorkflowStructuredRunConfig(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-run.v2"] = (
        "mineagent.oe-structured-provider-run.v2"
    )
    purpose: Literal["strategy_search"] = "strategy_search"
    engine: Literal["openevolve"] = "openevolve"
    search: StructuredSearchSpec
    fixed_inputs: FixedExecutionInputs
    model_binding: OEChatBinding
    runtime: StructuredRuntimePins
    budget: BudgetVector
    workflow_request_sha256: SHA
    workflow_context: WorkflowOEContext
    replay_policy: Literal["initial_attempt_only_no_process_replay"] = (
        "initial_attempt_only_no_process_replay"
    )

    @model_validator(mode="after")
    def exact(self):
        # Reuse the unchanged pure old shape validator only. This projection is
        # never persisted, submitted, returned as a receipt or used as authority.
        raw = _raw_fields(self)
        raw.pop("workflow_context")
        raw["authenticated_request_sha256"] = raw.pop("workflow_request_sha256")
        raw["schema_version"] = "mineagent.oe-structured-provider-run.v1"
        ProviderStructuredRunConfig.model_validate(raw)
        context = self.workflow_context
        binding = context.inputs.binding
        fixed = self.fixed_inputs
        if (self.search.owner_id, self.search.run_id) != (
            binding.owner_id,
            context.child_run_id,
        ):
            raise ValueError("Structured child differs from frozen stage binding")
        if (
            fixed.snapshot_id,
            fixed.snapshot_manifest_sha256,
            fixed.factor_version_id,
        ) != (
            context.snapshot_id,
            context.snapshot_manifest_sha256,
            context.factor_version_id,
        ):
            raise ValueError(
                "Structured input differs from actual predecessor commitments"
            )
        if fixed.release_receipt_ids != (context.release_exposure_id,):
            raise ValueError("Exact single predecessor snapshot release required")
        return self

    @property
    def sha256(self):
        return digest(self)


def parse_provider_run(raw):
    """Versioned closed data parser, never a database or process admission."""
    import json

    if type(raw) is not bytes or len(raw) > 1048576:
        raise ValueError("Bounded retained run bytes required")
    value = json.loads(raw)
    if canonical_bytes(value) != raw or type(value) is not dict:
        raise ValueError("Canonical unique run bytes required")
    if value.get("schema_version") == "mineagent.oe-structured-provider-run.v1":
        return ProviderStructuredRunConfig.model_validate_json(raw)
    if value.get("schema_version") == "mineagent.oe-structured-provider-run.v2":
        return WorkflowStructuredRunConfig.model_validate_json(raw)
    raise ValueError("Unknown structured provider run version")


def submission_request_sha256(config):
    checked = parse_provider_run(canonical_bytes(config))
    return (
        checked.workflow_request_sha256
        if type(checked) is WorkflowStructuredRunConfig
        else checked.authenticated_request_sha256
    )
