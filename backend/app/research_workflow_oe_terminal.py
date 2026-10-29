"""Actual successful OE stage closure; no status-only or result-release grant."""

from sqlalchemy import select

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_models import OEStructuredExecution
from backend.app.oe_structured_operational_models import (
    OEStructuredEpoch,
    OEStructuredProviderReceipt,
    OEStructuredProviderTransport,
)
from backend.app.oe_structured_provider_containment import provider_attempt_stopped
from backend.app.oe_structured_result_history import read_result_in_session
from backend.app.oe_structured_terminal_models import OEStructuredTerminalApplication
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.oe_workflow_saved import verify_workflow_oe_submission
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
)
from backend.app.research_workflow_models import (
    ResearchWorkflowModelConsumption,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_rd_terminal import _one, _settlements, unavailable
from backend.app.research_workflow_structured_public import (
    require_structured_workflow_schema,
)
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from packages.research_workflow.oe_checkpoint import (
    OEExecutionReference,
    OEStageCheckpoint,
    model_consumption_commitment,
    provider_receipt_commitment,
    terminal_application_commitment,
)


def _rows(session, model, scope, maximum):
    rows = list(session.scalars(select(model).filter_by(**scope).limit(maximum + 1)))
    if len(rows) > maximum:
        unavailable()
    return rows


def _mapping(row):
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


def read_oe_terminal_in_session(session, owner_id, workflow_id, stage_id, settings):
    require_structured_workflow_schema(session)
    require_native_storage_guard(settings.storage_root)
    stage = _one(
        session,
        ResearchWorkflowStage,
        owner_id=owner_id,
        workflow_id=workflow_id,
        id=stage_id,
    )
    attached = _one(
        session,
        OEWorkflowSubmission,
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
    )
    attached = verify_workflow_oe_submission(
        session, owner_id, attached.submission_id, settings
    )
    run = session.scalar(
        select(ResearchRun)
        .filter_by(owner_id=owner_id, id=stage.child_run_id)
        .with_for_update(nowait=True)
    )
    if (
        run is None
        or run.id != attached.run_id
        or run.status != "succeeded"
        or stage.kind != "structured_factor_execution"
    ):
        unavailable("workflow_waiting_child")
    _, config = decode_spec(session, run)
    if type(config) is not WorkflowStructuredRunConfig:
        unavailable()
    attempts = list(
        session.scalars(
            select(ExecutionAttempt)
            .filter_by(run_id=run.id)
            .with_for_update(nowait=True)
            .limit(2)
        )
    )
    if (
        len(attempts) != 1
        or attempts[0].id != run.active_attempt_id
        or attempts[0].generation != run.generation
        or attempts[0].status != "succeeded"
        or attempts[0].finished_at is None
        or not attempts[0].sandbox_stopped
    ):
        unavailable("workflow_waiting_containment")
    attempt = attempts[0]
    scope = {
        "owner_id": owner_id,
        "run_id": run.id,
        "attempt_id": attempt.id,
        "generation": attempt.generation,
    }
    if not provider_attempt_stopped(session, run, attempt, settings):
        unavailable("workflow_waiting_containment")
    epoch = _one(session, OEStructuredEpoch, **scope)
    executions = _rows(session, OEStructuredExecution, scope, 3)
    if not executions:
        unavailable()
    references = []
    for execution in sorted(executions, key=lambda row: str(row.id)):
        # This production reader reconstructs the complete actual OE graph and
        # protected bytes, not just the selected execution's stored metrics.
        actual = read_result_in_session(
            session, settings, owner_id=owner_id, execution_id=execution.id
        )
        if (
            actual.config_sha256 != config.sha256
            or actual.execution.run_id != run.id
            or actual.candidate_id != execution.candidate_id
            or actual.execution.snapshot_id != config.fixed_inputs.snapshot_id
        ):
            unavailable()
        references.append(
            OEExecutionReference(
                execution_id=execution.id,
                candidate_id=execution.candidate_id,
                program_version_id=execution.program_version_id,
                result_sha256=actual.execution.result_sha256,
                measurement_sha256=actual.measurement_sha256,
            )
        )
    admissions = _rows(session, ResearchProcessAdmission, scope, 4)
    if not admissions or epoch.controller_admission_id not in {
        row.id for row in admissions
    }:
        unavailable()
    stops = []
    for admission in admissions:
        stops.append(
            _one(
                session,
                ResearchProcessReceipt,
                **scope,
                admission_id=admission.id,
                kind="stop",
            )
        )
    controller_stop = next(
        row for row in stops if row.admission_id == epoch.controller_admission_id
    )
    members = _rows(session, OEStructuredProviderTransport, scope, 2)
    receipts = _rows(session, OEStructuredProviderReceipt, scope, 6)
    if len(members) != config.search.maximum_mutation_slots or len(receipts) != 3 * len(
        members
    ):
        unavailable()
    for member in members:
        if {row.kind for row in receipts if row.membership_id == member.id} != {
            "intent",
            "started",
            "stopped",
        }:
            unavailable()
    consumptions = _rows(session, ResearchWorkflowModelConsumption, scope, 2)
    expected = {
        (row.operation_id, row.transport_id, row.request_sha256) for row in members
    }
    if (
        len(consumptions) != len(members)
        or {
            (row.operation_id, row.transport_id, row.request_sha256)
            for row in consumptions
        }
        != expected
    ):
        unavailable()
    for row in consumptions:
        if (row.workflow_id, row.stage_id, row.input_revision_id, row.input_sha256) != (
            workflow_id,
            stage_id,
            stage.input_revision_id,
            config.workflow_context.inputs.sha256,
        ):
            unavailable()
    applications = _rows(session, OEStructuredTerminalApplication, scope, 3)
    if len(applications) != config.search.maximum_mutation_slots + 1 or {
        row.ordinal for row in applications
    } != set(range(len(applications))):
        unavailable()
    reservations = _settlements(session, scope)
    # Recheck current physical containment after all bounded reads.
    if not provider_attempt_stopped(session, run, attempt, settings):
        unavailable("workflow_waiting_containment")
    return OEStageCheckpoint(
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        input_revision_id=stage.input_revision_id,
        input_sha256=config.workflow_context.inputs.selection.sha256,
        definition_sha256=stage.definition_sha256,
        allocation_sha256=stage.allocation_sha256,
        workflow_submission_id=attached.id,
        child_submission_id=attached.submission_id,
        child_run_id=run.id,
        attempt_id=attempt.id,
        generation=attempt.generation,
        epoch_id=epoch.id,
        controller_admission_id=epoch.controller_admission_id,
        controller_stop_sha256=controller_stop.payload_sha256,
        process_admission_ids=tuple(sorted((row.id for row in admissions), key=str)),
        provider_transport_ids=tuple(sorted((row.id for row in members), key=str)),
        provider_receipts_sha256=provider_receipt_commitment(
            [_mapping(row) for row in members], [_mapping(row) for row in receipts]
        ),
        reservation_ids=reservations,
        executions=tuple(references),
        terminal_application_ids=tuple(
            row.id for row in sorted(applications, key=lambda row: row.ordinal)
        ),
        terminal_applications_sha256=terminal_application_commitment(
            [_mapping(row) for row in applications]
        ),
        model_consumption_sha256=model_consumption_commitment(
            [_mapping(row) for row in consumptions]
        ),
    )
