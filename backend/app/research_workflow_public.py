"""Server-authoritative public workflow preflight/create, flush-only and closed.

Exact accepted retries read the retained original plan before consulting current
operator configuration or selected-version activity. No child unit is launched.
"""

from dataclasses import dataclass

from sqlalchemy import select

from backend.app.rdagent_provider_declaration import prepare_provider_declaration
from backend.app.research_input_dispatch import PUBLICATION_PEAK_BYTES
from backend.app.research_input_models import ResearchInputObject
from backend.app.research_input_storage import read_input_object
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_admission import create_workflow, fail
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_input_reads import (
    resolve_workflow_inputs as resolve_model_input_versions,
)
from backend.app.research_workflow_models import ResearchWorkflow
from backend.app.research_workflow_public_schemas import (
    WorkflowPreflight,
    WorkflowRequest,
    WorkflowSubmit,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.admission import CreateWorkflow
from packages.research_workflow.plan import RDGuidancePlan


@dataclass(frozen=True)
class PreparedWorkflow:
    preflight: WorkflowPreflight
    plan: RDGuidancePlan


def prepare_workflow(session, owner_id, request, settings):
    """Call under shared storage lifecycle; derives every runtime pin on server."""
    request = WorkflowRequest.model_validate_json(canonical_bytes(request))
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    prepared = prepare_provider_declaration(
        session, owner_id, request.research, settings
    )
    resolved = resolve_model_input_versions(session, owner_id, request.refs, settings)
    if request.external_definition is not None:
        definition = request.external_definition
        obj = session.scalar(
            select(ResearchInputObject).filter_by(
                owner_id=owner_id, id=definition.raw_object_id
            )
        )
        if (
            obj is None
            or obj.kind != "raw_external"
            or obj.sha256 != definition.raw_sha256
        ):
            fail()
        read_input_object(settings.storage_root, obj, owner_id)
    profile = prepared.runtime_profile
    plan = RDGuidancePlan(
        owner_id=owner_id,
        mode=request.mode,
        maximum_iterations=request.maximum_iterations,
        normalize_external_first=request.external_definition is not None,
        research_template=prepared.config,
        objective=request.research.objective,
        source_policy=request.research.source_policy,
        qlib_installed_parent_image_id=profile.qlib_installed_parent_image_id,
        analysis_dependency_lock_sha256={
            lock.role: lock.sha256 for lock in profile.runtime.dependency_locks
        }["analysis"],
        initial_parent_factor_version_id=getattr(
            request.research, "parent_factor_version_id", None
        ),
        budget_ceiling=request.budget_ceiling,
        allow_workflow_development_feedback_release=request.allow_workflow_development_feedback_release,
    )
    if request.external_definition is not None:
        children = BudgetVector(
            **{
                k: getattr(plan.research_template.budget, k) * plan.maximum_iterations
                for k in BudgetVector.model_fields
            }
        )
        if not plan.budget_ceiling.covers(
            children.add(BudgetVector(storage_bytes=PUBLICATION_PEAK_BYTES))
        ):
            fail("workflow_budget_insufficient")
    commitment = {
        "schema_version": "mineagent.workflow-public-preflight-binding.v1",
        "owner_id": str(owner_id),
        "request_sha256": request.sha256,
        "plan_sha256": plan.sha256,
        "provider_preflight_sha256": prepared.preflight.preflight_sha256,
        "resolved_inputs_sha256": digest(resolved),
        "runtime_profile_sha256": digest(profile),
    }
    return PreparedWorkflow(
        WorkflowPreflight(
            **{
                k: v
                for k, v in commitment.items()
                if k not in {"schema_version", "owner_id"}
            },
            preflight_sha256=digest(commitment),
        ),
        plan,
    )


def submit_workflow(session, owner_id, body, settings):
    """Original public-body replay first; never retroactively re-resolve its plan."""
    body = WorkflowSubmit.model_validate_json(canonical_bytes(body))
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    original = session.scalar(
        select(ResearchWorkflow).filter_by(
            owner_id=owner_id, creation_key=body.idempotency_key
        )
    )
    raw = canonical_bytes(body).decode("ascii")
    if original is not None:
        retained = CreateWorkflow.model_validate_json(original.creation_canonical)
        if (
            retained.public_submission_canonical != raw
            or original.owner_id != owner_id
            or original.creation_key != body.idempotency_key
            or retained.idempotency_key != body.idempotency_key
            or canonical_bytes(retained).decode("ascii") != original.creation_canonical
            or digest(retained) != original.creation_sha256
        ):
            fail("workflow_command_conflict")
        # Existing internal replay revalidates original immutable revision/plan,
        # not today's active steer head, operator profile or disabled source.
        return create_workflow(session, owner_id, retained, settings)
    prepared = prepare_workflow(session, owner_id, body.request, settings)
    if prepared.preflight.preflight_sha256 != body.preflight_sha256:
        fail("workflow_preflight_changed")
    return create_workflow(
        session,
        owner_id,
        CreateWorkflow(
            idempotency_key=body.idempotency_key,
            plan=prepared.plan,
            refs=body.request.refs,
            external_definition=body.request.external_definition,
            public_submission_canonical=raw,
        ),
        settings,
    )
