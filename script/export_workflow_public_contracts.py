"""Export actual unregistered workflow DTOs, never endpoint availability."""

import json
from pathlib import Path

from backend.app.research_workflow_public_schemas import (
    WorkflowPreflight,
    WorkflowRequest,
    WorkflowSubmit,
)
from backend.app.research_workflow_routes import (
    WorkflowAvailability,
    WorkflowManualOptions,
)
from backend.app.research_workflow_stage_list import WorkflowStageList
from backend.app.research_workflow_structured_schemas import (
    StructuredWorkflowPreflight,
    StructuredWorkflowRequest,
    StructuredWorkflowSubmit,
)
from packages.research_workflow.admission import SelectWorkflowInputs, WorkflowCreated
from packages.research_workflow.contracts import (
    WorkflowCommand,
    WorkflowCommandReceipt,
    WorkflowInputSnapshot,
    WorkflowStageHistory,
    WorkflowView,
)
from packages.research_workflow.oe_history import (
    StructuredWorkflowView,
    WorkflowOEStageHistory,
)


def export():
    components = {}
    for model in (
        WorkflowAvailability,
        WorkflowRequest,
        WorkflowSubmit,
        WorkflowPreflight,
        WorkflowCreated,
        WorkflowManualOptions,
        SelectWorkflowInputs,
        WorkflowCommand,
        WorkflowCommandReceipt,
        WorkflowInputSnapshot,
        WorkflowStageHistory,
        WorkflowStageList,
        WorkflowView,
        StructuredWorkflowRequest,
        StructuredWorkflowSubmit,
        StructuredWorkflowPreflight,
        StructuredWorkflowView,
        WorkflowOEStageHistory,
    ):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        definitions = schema.pop("$defs", {})
        definitions[model.__name__] = schema
        for name, value in definitions.items():
            if name in components and components[name] != value:
                raise ValueError("Conflicting workflow DTO definitions")
            components[name] = value
    target = (
        Path(__file__).resolve().parents[1] / "packages/contracts/workflow-public.json"
    )
    target.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {
                    "title": "Unregistered workflow public contract",
                    "version": "1",
                },
                "paths": {},
                "components": {"schemas": components},
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    return target


if __name__ == "__main__":
    print(export())
