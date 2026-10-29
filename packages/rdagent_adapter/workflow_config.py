"""028 data-only workflow child declaration; old v2 manual runs stay unchanged."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Sha256
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.seed_parent import SeedParentReference
from packages.research_workflow.model_inputs import ModelStageInputSnapshot


class WorkflowFactorResearchConfig(ProviderFactorResearchConfig):
    controller_profile: Literal["mineagent.rdagent-orchestrator.v3"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    schema_version: Literal["mineagent.factor-research.v3"] = (
        "mineagent.factor-research.v3"
    )
    model_profile: Literal["workflow.rdagent.v1"] = "workflow.rdagent.v1"
    workflow_child_run_id: UUID
    workflow_inputs: ModelStageInputSnapshot
    workflow_inputs_sha256: Sha256
    workflow_parent: SeedParentReference | None
    # Missing in retained v3 configurations: never add a default byte to their hash.
    workflow_prompt_layout: Literal["generation_user.v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="wrap")
    @classmethod
    def no_explicit_null_layout(cls, value, handler):
        if isinstance(value, cls):
            for name in ("workflow_prompt_layout", "controller_profile"):
                if name in value.model_fields_set and getattr(value, name) is None:
                    raise ValueError(
                        "Absent optional selector cannot be explicitly null"
                    )
            # Revalidate all fields without inventing a legacy null serialization.
            value = value.model_dump(mode="python")
        if (
            isinstance(value, dict)
            and "workflow_prompt_layout" in value
            and value["workflow_prompt_layout"] is None
        ):
            raise ValueError(
                "Legacy layout is represented by an absent field, never null"
            )
        return handler(value)

    @model_validator(mode="after")
    def exact_workflow_inputs(self):
        binding = self.workflow_inputs.binding
        if self.workflow_inputs.sha256 != self.workflow_inputs_sha256:
            raise ValueError("Workflow resolved input hash differs")
        if (
            self.steer_snapshot.owner_id != binding.owner_id
            or self.steer_snapshot.versions
        ):
            raise ValueError("Workflow guidance must occur once in combined inputs")
        if self.max_iterations != 1:
            raise ValueError(
                "Each workflow child retains the original single generation bound"
            )
        if (
            self.workflow_parent is not None
            and self.workflow_parent.owner_id != binding.owner_id
        ):
            raise ValueError("Workflow parent owner differs")
        return self
