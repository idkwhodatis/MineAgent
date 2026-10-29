"""Closed version dispatch; callers separately require installed capabilities."""

import json

from backend.app.research_workflow_structured_schemas import CreateStructuredWorkflow
from packages.factors.contracts import canonical_bytes
from packages.research_workflow.admission import CreateWorkflow


def parse_workflow_creation(raw):
    if type(raw) is not bytes or len(raw) > 1100000:
        raise ValueError("Exact bounded creation bytes required")
    value = json.loads(raw)
    if canonical_bytes(value) != raw or type(value) is not dict:
        raise ValueError("Canonical unique workflow creation required")
    version = value.get("schema_version")
    if version == "mineagent.workflow-create.v1":
        return CreateWorkflow.model_validate_json(raw)
    if version == "mineagent.workflow-create.v2":
        return CreateStructuredWorkflow.model_validate_json(raw)
    raise ValueError("Unknown workflow creation version")


def read_guidance_plan(session, raw):
    from packages.research_workflow.structured_plan import (
        RDStructuredGuidancePlan,
        parse_guidance_plan,
    )

    if type(raw) is not str:
        raise ValueError("Retained plan text required")
    plan = parse_guidance_plan(raw.encode("ascii"))
    if type(plan) is RDStructuredGuidancePlan:
        from backend.app.research_workflow_structured_public import (
            require_structured_workflow_schema,
        )

        require_structured_workflow_schema(session)
    return plan
