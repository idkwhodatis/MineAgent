"""Fresh actual workflow child qualification at normal outbox claim."""

from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import select

from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_preflight import budget_floor, freeze_inputs
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.oe_workflow_input import retained_workflow_submission_data
from backend.app.oe_workflow_saved import verify_workflow_oe_submission
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest


def fail():
    raise DomainError(
        "workflow_oe_queued_binding_changed",
        "Current owned workflow child binding differs",
        409,
    )


def dispatch_binding(session, run, row, config, settings):
    require_structured_workflow_schema(session)
    request = WorkflowStructuredSearchRequest.model_validate_json(row.request_canonical)
    if (
        row.origin != "workflow_stage"
        or canonical_bytes(request).decode() != row.request_canonical
        or row.request_sha256 != request.sha256
        or config.workflow_request_sha256 != request.sha256
        or request.workflow_context != config.workflow_context
    ):
        fail()
    runtime = resolve_runtime(settings)
    _, binding = resolve_operator_profile(
        settings, run.owner_id, request.model_profile_id
    )
    if runtime.runtime != config.runtime or binding != config.model_binding:
        fail()
    return request, config, row.preflight_sha256


@contextmanager
def claim_data(session, owner_id, request, settings, config, submission_id):
    with native_storage_guard(settings.storage_root):
        row = verify_workflow_oe_submission(session, owner_id, submission_id, settings)
        root = session.scalar(
            select(ResearchWorkflow)
            .filter_by(owner_id=owner_id, id=row.workflow_id)
            .with_for_update(nowait=True)
        )
        stage = session.scalar(
            select(ResearchWorkflowStage).filter_by(
                owner_id=owner_id, workflow_id=row.workflow_id, id=row.stage_id
            )
        )
        if (
            root is None
            or stage is None
            or root.current_stage_id != stage.id
            or root.status not in {"running", "pause_requested", "cancel_requested"}
            or stage.child_run_id != row.run_id
        ):
            fail()
        # The stage was already atomically admitted. Future selection edits do
        # not rewrite it; pause/cancel stops after its real closed boundary.
        with retained_workflow_submission_data(
            session, owner_id, request, settings
        ) as data:
            yield data


def evaluate_claim(session, owner_id, request, settings, data):
    require_structured_workflow_schema(session)
    runtime = resolve_runtime(settings)
    _, binding = resolve_operator_profile(settings, owner_id, request.model_profile_id)
    request.require_binding(binding)
    if not request.budget.covers(budget_floor(request, binding)):
        fail()
    fixed = freeze_inputs(data)
    sha = digest(
        {
            "schema_version": "mineagent.workflow-oe-preflight.v1",
            "request_sha256": request.sha256,
            "fixed_input_sha256": digest(fixed),
            "runtime_profile_sha256": digest(runtime),
            "model_binding_sha256": binding.sha256,
        }
    )
    return (
        SimpleNamespace(can_submit=True, preflight_sha256=sha),
        fixed,
        runtime,
        binding,
    )
