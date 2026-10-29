"""028 controller input binds the new child run and actual frozen stage inputs."""

from typing import Literal

from pydantic import model_validator

from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.rdagent_adapter.workflow_prompt import first_workflow_messages


class RDWorkflowOperationalBootstrap(RDOperationalBootstrap):
    schema_version: Literal["mineagent.rdagent-workflow-operational-bootstrap.v1"] = (
        "mineagent.rdagent-workflow-operational-bootstrap.v1"
    )
    config: WorkflowFactorResearchConfig
    continuation: Literal["workflow_stage_new_process"] = "workflow_stage_new_process"
    seed: ResolvedSeedParent | None

    @model_validator(mode="after")
    def exact_child_stage(self):
        if (self.run_id, self.owner_id) != (
            self.config.workflow_child_run_id,
            self.config.workflow_inputs.binding.owner_id,
        ):
            raise ValueError("Workflow bootstrap owner/new child run differs")
        first_workflow_messages(self.config, self.seed)
        return self


def parse_workflow_bootstrap(raw):
    """Explicit028 runtime parser; legacy parser remains closed to workflow v3."""
    import json

    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.seeded_bootstrap import parse_bootstrap

    if type(raw) is not bytes or not 1 <= len(raw) <= 262144:
        raise ValueError("Bounded controller bootstrap bytes required")
    document = json.loads(raw)
    if type(document) is not dict:
        raise ValueError("Controller bootstrap object required")
    if (
        document.get("schema_version")
        != "mineagent.rdagent-workflow-operational-bootstrap.v1"
    ):
        return parse_bootstrap(raw)
    value = RDWorkflowOperationalBootstrap.model_validate_json(raw)
    if canonical_bytes(value) != raw:
        raise ValueError("Canonical duplicate-free workflow bootstrap required")
    return value
