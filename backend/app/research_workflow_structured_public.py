"""Server-frozen RD→OE root declaration, never an implicit output release."""

from sqlalchemy import select, text

from backend.app.oe_structured_preflight import budget_floor
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.research_input_dispatch import PUBLICATION_PEAK_BYTES
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_admission import create_workflow, fail
from backend.app.research_workflow_models import ResearchWorkflow
from backend.app.research_workflow_public import PreparedWorkflow, prepare_workflow
from backend.app.research_workflow_public_schemas import WorkflowRequest
from backend.app.research_workflow_structured_schemas import (
    CreateStructuredWorkflow,
    StructuredWorkflowPreflight,
    StructuredWorkflowRequest,
    StructuredWorkflowSubmit,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.structured_plan import (
    RDStructuredGuidancePlan,
    WorkflowStructuredTemplate,
)

REVISION = "031_workflow_structured_stage"


def require_structured_workflow_schema(session):
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        [REVISION],
        ["032_normal_oe_note_pins"],
    ):
        fail("workflow_structured_schema_unavailable")


def prepare_structured_workflow(session, owner_id, request, settings):
    request = StructuredWorkflowRequest.model_validate_json(canonical_bytes(request))
    require_structured_workflow_schema(session)
    raw = request.model_dump(mode="json")
    raw.pop("structured")
    raw["schema_version"] = "mineagent.workflow-public-request.v1"
    base = prepare_workflow(
        session,
        owner_id,
        WorkflowRequest.model_validate_json(canonical_bytes(raw)),
        settings,
    )
    choice = request.structured
    runtime = resolve_runtime(settings)
    _, binding = resolve_operator_profile(settings, owner_id, choice.model_profile_id)
    if binding.sha256 != choice.model_binding_sha256:
        fail("workflow_structured_binding_changed")
    if not choice.budget.covers(budget_floor(choice, binding)):
        fail("workflow_budget_insufficient")
    template = WorkflowStructuredTemplate.model_validate_json(
        canonical_bytes(
            {
                "seed_parameters": choice.seed_parameters.model_dump(mode="json"),
                "objective": choice.objective.model_dump(mode="json"),
                "model_binding": binding.model_dump(mode="json"),
                "runtime": runtime.runtime.model_dump(mode="json"),
                "budget": choice.budget.model_dump(mode="json"),
                "maximum_mutation_slots": choice.maximum_mutation_slots,
            }
        )
    )
    raw = base.plan.model_dump(mode="json")
    raw.update(
        schema_version="mineagent.rd-structured-guidance-workflow-plan.v1",
        profile="rd_guidance_then_structured_oe.v1",
        structured_template=template.model_dump(mode="json"),
    )
    plan = RDStructuredGuidancePlan.model_validate_json(canonical_bytes(raw))
    if request.external_definition is not None:
        required = BudgetVector(
            **{
                k: getattr(plan.research_template.budget, k) * plan.maximum_iterations
                for k in BudgetVector.model_fields
            }
        )
        required = required.add(template.budget).add(
            BudgetVector(
                storage_bytes=PUBLICATION_PEAK_BYTES
                + 65536 * (plan.maximum_iterations - 1)
            )
        )
        if not plan.budget_ceiling.covers(required):
            fail("workflow_budget_insufficient")
    commitment = {
        "schema_version": "mineagent.workflow-public-preflight-binding.v2",
        "owner_id": str(owner_id),
        "request_sha256": request.sha256,
        "plan_sha256": plan.sha256,
        "provider_preflight_sha256": base.preflight.provider_preflight_sha256,
        "resolved_inputs_sha256": base.preflight.resolved_inputs_sha256,
        "runtime_profile_sha256": base.preflight.runtime_profile_sha256,
        "structured_template_sha256": digest(template),
    }
    preflight = StructuredWorkflowPreflight(
        **{
            k: v
            for k, v in commitment.items()
            if k not in {"schema_version", "owner_id"}
        },
        preflight_sha256=digest(commitment),
    )
    return PreparedWorkflow(preflight, plan)


def submit_structured_workflow(session, owner_id, body, settings):
    body = StructuredWorkflowSubmit.model_validate_json(canonical_bytes(body))
    require_structured_workflow_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    original = session.scalar(
        select(ResearchWorkflow).filter_by(
            owner_id=owner_id, creation_key=body.idempotency_key
        )
    )
    raw = canonical_bytes(body).decode("ascii")
    if original is not None:
        retained = CreateStructuredWorkflow.model_validate_json(
            original.creation_canonical
        )
        if (
            retained.public_submission_canonical != raw
            or canonical_bytes(retained).decode() != original.creation_canonical
            or digest(retained) != original.creation_sha256
        ):
            fail("workflow_command_conflict")
        return create_workflow(session, owner_id, retained, settings)
    prepared = prepare_structured_workflow(session, owner_id, body.request, settings)
    if prepared.preflight.preflight_sha256 != body.preflight_sha256:
        fail("workflow_preflight_changed")
    return create_workflow(
        session,
        owner_id,
        CreateStructuredWorkflow(
            idempotency_key=body.idempotency_key,
            plan=prepared.plan,
            refs=body.request.refs,
            external_definition=body.request.external_definition,
            public_submission_canonical=raw,
        ),
        settings,
    )
