"""Workflow-owned fresh RD template; seeded v4 is not workflow authority."""

from typing import Literal

from pydantic import Field, model_validator

from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig


class WorkflowProviderResearchTemplate(ProviderFactorResearchConfig):
    controller_profile: Literal["mineagent.rdagent-orchestrator.v3"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def revalidate_provider_declaration(cls, value):
        if isinstance(value, ProviderFactorResearchConfig):
            return ProviderFactorResearchConfig.model_validate(value).model_dump(
                mode="python"
            )
        return value
