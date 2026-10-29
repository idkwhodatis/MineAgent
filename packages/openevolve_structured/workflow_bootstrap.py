"""Explicit workflow controller bootstrap; no endpoint, key or release grant."""

from typing import Literal

from pydantic import model_validator

from packages.factors.contracts import digest

from .contracts import SHA
from .provider_bootstrap import ProviderStructuredBootstrap, runtime_profile_sha256
from .workflow_context import WorkflowOEContext


def workflow_runtime_profile_sha256():
    return digest(
        {
            "schema_version": "mineagent.oe-structured-workflow-runtime-profile.v1",
            "base_profile_sha256": runtime_profile_sha256(),
            "prompt_policy": "complete_selected_evidence_before_observation.v1",
            "budget_policy": "unchanged_base_calls_tokens_bytes_deadlines",
            "resume": False,
        }
    )


class WorkflowStructuredBootstrap(ProviderStructuredBootstrap):
    schema_version: Literal["mineagent.oe-structured-bootstrap.v3"] = (
        "mineagent.oe-structured-bootstrap.v3"
    )
    workflow_context: WorkflowOEContext
    workflow_context_sha256: SHA

    @model_validator(mode="after")
    def workflow_binding(self):
        context = self.workflow_context
        if self.workflow_context_sha256 != digest(context):
            raise ValueError("Exact selected workflow context required")
        if (self.spec.owner_id, self.spec.run_id) != (
            context.inputs.binding.owner_id,
            context.child_run_id,
        ):
            raise ValueError("Workflow bootstrap differs from frozen child")
        if self.runtime_profile_sha256 != workflow_runtime_profile_sha256():
            raise ValueError("Exact explicit workflow runtime profile required")
        return self


def parse_provider_bootstrap(raw):
    import json

    from packages.factors.contracts import canonical_bytes

    if type(raw) is not bytes or len(raw) > 262144:
        raise ValueError("Bounded retained bootstrap bytes required")
    value = json.loads(raw)
    if type(value) is not dict or canonical_bytes(value) != raw:
        raise ValueError("Canonical unique bootstrap required")
    if value.get("schema_version") == "mineagent.oe-structured-bootstrap.v2":
        return ProviderStructuredBootstrap.model_validate_json(raw)
    if value.get("schema_version") == "mineagent.oe-structured-bootstrap.v3":
        return WorkflowStructuredBootstrap.model_validate_json(raw)
    raise ValueError("Unknown provider bootstrap version")
