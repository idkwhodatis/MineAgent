"""Owner-scoped normal027 status/read, not legacy or024 Python fallback.

Metadata and authenticated input only. This endpoint cannot release derived
values, manufacture a candidate/metric, or use retained receipts to launch work.
"""

import json

from sqlalchemy import select

from backend.app.models import ResearchRun
from backend.app.oe_structured_control_services import controls_for
from backend.app.oe_structured_identity import decode_spec, fail
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchSubmit,
)
from backend.app.oe_structured_run_schemas import (
    ProviderStructuredRunView,
    StructuredRunActions,
    WorkflowStructuredRunOrigin,
    WorkflowStructuredRunView,
)
from backend.app.oe_structured_submission_receipts import existing_receipt
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_runtime.budget_contracts import BudgetVector


def read_owned_run(session, owner_id, run_id):
    run = session.scalar(
        select(ResearchRun).where(
            ResearchRun.owner_id == owner_id, ResearchRun.id == run_id
        )
    )
    if run is None or run.owner_id != owner_id or run.id != run_id:
        raise DomainError("run_not_found", "Structured search run not found", 404)
    spec, config = decode_spec(session, run)
    declaration = session.get(OEStructuredSubmission, spec.submission_id)
    if declaration is None or (declaration.owner_id, declaration.run_id) != (
        owner_id,
        run_id,
    ):
        fail()
    if type(config) is WorkflowStructuredRunConfig:
        from backend.app.oe_workflow_metadata import require_attachment
        from backend.app.oe_workflow_submission_schemas import (
            WorkflowStructuredSearchRequest,
        )

        attachment = require_attachment(session, run, spec, config)
        request = WorkflowStructuredSearchRequest.model_validate_json(
            spec.request_canonical
        )
        if (
            declaration.origin != "workflow_stage"
            or canonical_bytes(request.workflow_context)
            != canonical_bytes(config.workflow_context)
            or attachment.submission_id != declaration.id
            or (attachment.request_sha256, attachment.preflight_sha256)
            != (spec.request_sha256, spec.preflight_sha256)
        ):
            fail()
        receipt = WorkflowStructuredRunOrigin(
            id=declaration.id,
            run_id=run.id,
            owner_id=run.owner_id,
            workflow_id=attachment.workflow_id,
            stage_id=attachment.stage_id,
            input_revision_id=attachment.input_revision_id,
            snapshot_id=spec.snapshot_id,
            request_sha256=spec.request_sha256,
            preflight_sha256=spec.preflight_sha256,
            required_process_units=request.required_process_units,
        )
        view_type = WorkflowStructuredRunView
        # Workflow control stays at its root. Child metadata grants no direct
        # cancel/pause/resume API, nor does it claim immediate stage termination.
        actions = StructuredRunActions()
    else:
        # Same strict accepted-replay decoder, no runtime lookup or key mutation.
        submitted = ProviderStructuredSearchSubmit.model_validate_json(
            json.dumps(
                {**spec.request, "preflight_sha256": spec.preflight_sha256},
                allow_nan=False,
            )
        )
        receipt = existing_receipt(
            session, owner_id, declaration.idempotency_key, submitted
        )
        if receipt is None or (receipt.id, receipt.run_id) != (declaration.id, run_id):
            fail()
        view_type = ProviderStructuredRunView
        actions = controls_for(session, run)
    budget = session.scalar(
        select(ResearchBudgetControl).where(
            ResearchBudgetControl.owner_id == owner_id,
            ResearchBudgetControl.run_id == run_id,
        )
    )
    if budget is None or (budget.owner_id, budget.run_id) != (owner_id, run_id):
        fail()
    try:
        vectors = []
        for raw in (budget.limits, budget.used, budget.reserved):
            if type(raw) is not dict or set(raw) != set(BudgetVector.model_fields):
                fail()
            if any(type(value) is not int for value in raw.values()):
                fail()
            vectors.append(BudgetVector.model_validate(raw))
        limits, used, reserved = vectors
        if limits != config.budget or not limits.covers(used.add(reserved)):
            fail()
        return view_type(
            id=run.id,
            status=run.status,
            generation=run.generation,
            active_attempt_id=run.active_attempt_id,
            created_at=run.created_at,
            updated_at=run.updated_at,
            submission=receipt,
            factor_version_id=config.fixed_inputs.factor_version_id,
            dataset_version_id=config.fixed_inputs.dataset_version_id,
            model_binding=config.model_binding,
            budget_limits=limits,
            budget_used=used,
            budget_reserved=reserved,
            declared_process_units=receipt.required_process_units,
            data_qualification=config.fixed_inputs.market_qualification,
            actions=actions,
        )
    except (ValueError, TypeError, AttributeError):
        fail()
