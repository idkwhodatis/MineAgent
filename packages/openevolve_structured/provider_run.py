"""Host-frozen027 normal run declaration; public metadata is never launch authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import digest
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.research_runtime.budget_contracts import BudgetVector

from .candidate import Closed, _raw_fields
from .contracts import SHA, StructuredSearchSpec
from .provider_contracts import OEChatBinding


class FixedExecutionInputs(Closed):
    schema_version: Literal[
        "mineagent.oe-structured-retained-inputs.v1",
        "mineagent.oe-structured-retained-inputs.v2",
    ] = "mineagent.oe-structured-retained-inputs.v1"
    snapshot_id: UUID
    snapshot_manifest_sha256: SHA
    snapshot_input_sha256: SHA
    snapshot_output_sha256: SHA
    factor_version_id: UUID
    dataset_version_id: UUID
    dataset_sha256: SHA
    source_policy: ConfiguredSourcePolicy
    market_sha256: SHA
    market_size_bytes: int = Field(ge=1, le=33554432)
    release_receipt_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    # This first normal profile supports the explicit synthetic generator only.
    # A declared-upload successor must retain its different qualification.
    market_qualification: Literal[
        "synthetic_by_construction", "user_declaration_unverified"
    ] = "synthetic_by_construction"

    @model_validator(mode="after")
    def exact(self):
        if (self.schema_version, self.market_qualification) not in {
            ("mineagent.oe-structured-retained-inputs.v1", "synthetic_by_construction"),
            (
                "mineagent.oe-structured-retained-inputs.v2",
                "user_declaration_unverified",
            ),
        }:
            raise ValueError("Exact versioned market qualification required")
        ConfiguredSourcePolicy.model_validate(_raw_fields(self.source_policy))
        if len(set(self.release_receipt_ids)) != len(self.release_receipt_ids):
            raise ValueError("Duplicate release receipt")
        return self


class StructuredRuntimePins(Closed):
    controller_image_id: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    controller_installed_parent_image_id: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    controller_inventory_sha256: SHA
    engine_image_id: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    engine_installed_parent_image_id: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    engine_dependency_lock_sha256: SHA


class ProviderStructuredRunConfig(Closed):
    schema_version: Literal["mineagent.oe-structured-provider-run.v1"] = (
        "mineagent.oe-structured-provider-run.v1"
    )
    purpose: Literal["strategy_search"] = "strategy_search"
    engine: Literal["openevolve"] = "openevolve"
    search: StructuredSearchSpec
    fixed_inputs: FixedExecutionInputs
    model_binding: OEChatBinding
    runtime: StructuredRuntimePins
    budget: BudgetVector
    authenticated_request_sha256: SHA
    replay_policy: Literal["initial_attempt_only_no_process_replay"] = (
        "initial_attempt_only_no_process_replay"
    )

    @model_validator(mode="after")
    def exact(self):
        search = StructuredSearchSpec.model_validate(_raw_fields(self.search))
        inputs = FixedExecutionInputs.model_validate(_raw_fields(self.fixed_inputs))
        OEChatBinding.model_validate(_raw_fields(self.model_binding))
        StructuredRuntimePins.model_validate(_raw_fields(self.runtime))
        raw = _raw_fields(self.budget)
        if any(type(value) is not int for value in raw.values()):
            raise ValueError("Exact integer budgets required")
        BudgetVector.model_validate(raw)
        if search.execution_binding_sha256 != digest(inputs):
            raise ValueError("Search declaration differs from retained fixed input")
        return self
