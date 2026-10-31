"""Actual successful RD child boundary, not a status-derived completion grant.

This is an internal integrity reader. It emits only immutable references/hashes;
result disclosure and feedback selection still require the existing ledger.
"""

from sqlalchemy import select

from backend.app.execution_snapshot_history import historical_session
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotPublication,
    FactorExecutionEvaluationOutcome,
)
from backend.app.factor_analysis_history import read_analysis_in_session
from backend.app.factor_analysis_models import (
    FactorDirectAnalysisIntent,
    FactorDirectAnalysisOutcome,
)
from backend.app.factor_models import FactorVersion
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.rdagent_boundary_models import FactorGenerationBoundary
from backend.app.rdagent_controller_history import read_historical_controller_records
from backend.app.rdagent_controller_stop import controller_stop_committed
from backend.app.rdagent_models import (
    FactorControllerLaunch,
    FactorEvaluationStart,
    FactorGenerationSource,
    FactorGenerationStart,
)
from backend.app.rdagent_provider_containment import provider_attempt_stopped
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.rdagent_workflow_saved import (
    verify_workflow_submission_under_native_guard,
)
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
)
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.rdagent_adapter.workflow_bootstrap import RDWorkflowOperationalBootstrap
from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def unavailable(code="workflow_child_terminal_changed"):
    raise DomainError(code, "Complete owned child boundary is not available", 409)


def _one(session, model, **filters):
    rows = list(session.scalars(select(model).filter_by(**filters).limit(2)))
    if len(rows) != 1:
        unavailable()
    return rows[0]


def _scope(row, reference):
    if row is None or any(
        getattr(row, key) != getattr(reference, key) for key in SCOPE
    ):
        unavailable()
    return row


def _settlements(session, scope):
    reservations = list(
        session.scalars(select(ResearchBudgetReservation).filter_by(**scope).limit(257))
    )
    settlements = list(
        session.scalars(select(ResearchBudgetSettlement).filter_by(**scope).limit(257))
    )
    if not reservations or len(reservations) > 256 or len(settlements) > 256:
        unavailable("workflow_waiting_containment")
    by_reservation = {row.reservation_id: row for row in settlements}
    if len(by_reservation) != len(settlements) or set(by_reservation) != {
        row.id for row in reservations
    }:
        unavailable("workflow_waiting_containment")
    for reservation in reservations:
        settlement = by_reservation[reservation.id]
        if (
            reservation.state != "settled"
            or settlement.outcome != "conservative_unknown"
            or BudgetVector.model_validate(reservation.amounts)
            != BudgetVector.model_validate(settlement.charge)
        ):
            unavailable("workflow_waiting_containment")
    return tuple(sorted((row.id for row in reservations), key=str))


def read_rd_terminal_in_session(session, owner_id, workflow_id, stage_id, settings):
    """Fresh complete integrity read under historical storage/run/attempt locks."""
    require_schema(session)
    require_native_storage_guard(settings.storage_root)
    root = _one(session, ResearchWorkflow, owner_id=owner_id, id=workflow_id)
    stage = _one(
        session,
        ResearchWorkflowStage,
        owner_id=owner_id,
        workflow_id=workflow_id,
        id=stage_id,
    )
    if stage.kind != "rd_factor_generation" or stage.child_run_id is None:
        unavailable()
    receipt = _one(
        session,
        FactorWorkflowSubmission,
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
    )
    receipt = verify_workflow_submission_under_native_guard(
        session, owner_id, receipt.id, settings
    )
    run = session.scalar(
        select(ResearchRun)
        .filter_by(owner_id=owner_id, id=stage.child_run_id)
        .with_for_update(nowait=True)
    )
    if (
        run is None
        or run.id != receipt.run_id
        or run.engine != "rdagent"
        or run.purpose != "factor_research"
        or run.status != "succeeded"
    ):
        unavailable("workflow_waiting_child")
    config = WorkflowFactorResearchConfig.model_validate_json(
        canonical_bytes(run.config)
    )
    if config.workflow_child_run_id != run.id:
        unavailable()
    attempts = list(
        session.scalars(
            select(ExecutionAttempt)
            .filter_by(run_id=run.id)
            .with_for_update(nowait=True)
            .limit(2)
        )
    )
    # Each supported workflow child is a new bounded process, not a historical
    # run reattachment/retry. A future retry protocol must close every attempt.
    if (
        len(attempts) != 1
        or attempts[0].id != run.active_attempt_id
        or attempts[0].generation != run.generation
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
    # A succeeded label cannot bypass unresolved real SDK containment.
    if not provider_attempt_stopped(session, run, attempt, settings):
        unavailable("workflow_waiting_containment")
    start = _one(session, FactorGenerationStart, **scope)
    source = _one(session, FactorGenerationSource, **scope, start_id=start.id)
    evaluation = _one(session, FactorEvaluationStart, **scope, start_id=start.id)
    evaluated = _one(
        session, FactorExecutionEvaluationOutcome, **scope, evaluation_id=evaluation.id
    )
    publication = _one(
        session, ExecutionSnapshotPublication, **scope, evaluation_id=evaluation.id
    )
    analysis = _one(
        session,
        FactorDirectAnalysisIntent,
        **scope,
        plan_id=receipt.plan_id,
        snapshot_id=publication.id,
    )
    outcome = session.get(FactorDirectAnalysisOutcome, analysis.id)
    _scope(outcome, analysis)
    version = _one(
        session, FactorVersion, owner_id=owner_id, id=source.factor_version_id
    )
    expected_parent = (
        None
        if config.workflow_parent is None
        else config.workflow_parent.factor_version_id
    )
    if (
        start.spec_id != receipt.spec_id
        or start.logical_step != 0
        or start.task_index != 0
        or start.parent_factor_version_id != expected_parent
        or version.parent_version_id != expected_parent
        or (config.workflow_parent is None and version.ordinal != 1)
        or (
            config.workflow_parent is not None
            and (
                version.factor_id != config.workflow_parent.factor_id
                or version.ordinal <= config.workflow_parent.ordinal
            )
        )
        or version.source_kind != "rdagent"
        or version.code_sha256 != source.source_sha256
        or evaluation.evaluated_source_id != source.id
        or evaluation.factor_version_id != version.id
        or evaluated.status != "valid"
        or evaluated.generation_start_id != start.id
        or evaluated.snapshot_id != publication.id
        or (publication.start_id, publication.source_id, publication.factor_version_id)
        != (start.id, source.id, version.id)
    ):
        unavailable()
    manifest = ExecutionSnapshotManifest.model_validate_json(
        canonical_bytes(publication.manifest)
    )
    if (
        manifest.owner_id != owner_id
        or manifest.run_id != run.id
        or manifest.snapshot_id != publication.id
        or manifest.evaluation_id != evaluation.id
        or manifest.generation_start_id != start.id
        or manifest.attempt_id != attempt.id
        or manifest.generation != attempt.generation
        or manifest.source_id != source.id
        or digest(manifest) != publication.manifest_sha256
        or manifest.input.version.factor_version_id != version.id
        or manifest.input.version.code_sha256 != source.source_sha256
        or evaluated.snapshot_manifest_sha256 != publication.manifest_sha256
        or evaluated.snapshot_input_sha256 != manifest.input_sha256
        or evaluated.snapshot_output_sha256 != manifest.output_rows_sha256
    ):
        unavailable()
    controller = _one(session, FactorControllerLaunch, **scope)
    boundary = _one(
        session,
        FactorGenerationBoundary,
        **scope,
        id=evaluated.boundary_id,
        start_id=start.id,
        controller_launch_id=controller.id,
    )
    if (
        evaluated.controller_launch_id != controller.id
        or boundary.logical_step != start.logical_step
        or boundary.boundary != "returned"
        or boundary.next_logical_step is not None
    ):
        unavailable()
    bootstrap = RDWorkflowOperationalBootstrap.model_validate_json(
        canonical_bytes(controller.bootstrap)
    )
    if (
        bootstrap.config != config
        or bootstrap.sha256 != controller.bootstrap_sha256
        or (
            bootstrap.owner_id,
            bootstrap.run_id,
            bootstrap.attempt_id,
            bootstrap.generation,
            bootstrap.spec_id,
        )
        != (owner_id, run.id, attempt.id, attempt.generation, receipt.spec_id)
    ):
        unavailable()
    admissions = list(
        session.scalars(select(ResearchProcessAdmission).filter_by(**scope).limit(19))
    )
    expected = {
        controller.admission_id: "rd_orchestrator",
        outcome.admission_id: "native_evaluator",
    }
    for part in manifest.partitions:
        if (
            part.candidate_admission_id in expected
            or part.normalizer_admission_id in expected
        ):
            unavailable()
        expected[part.candidate_admission_id] = "factor_cutoff_candidate"
        expected[part.normalizer_admission_id] = "factor_normalizer"
    if (
        not 4 <= len(admissions) <= 18
        or len(admissions) != len(expected)
        or {row.id: row.role for row in admissions} != expected
    ):
        unavailable("workflow_waiting_containment")
    controller_admission = next(
        row for row in admissions if row.id == controller.admission_id
    )
    if (
        controller_admission.input_sha256 != bootstrap.sha256
        or controller_admission.image_id != config.runtime.orchestrator_image
        or not controller_stop_committed(session, controller_admission)
    ):
        unavailable("workflow_waiting_containment")
    receipts = list(
        session.scalars(
            select(ResearchProcessReceipt)
            .filter_by(admission_id=controller.admission_id)
            .limit(5)
        )
    )
    by_kind = {row.kind: row for row in receipts}
    if len(receipts) != 4 or set(by_kind) != {
        "launch_intent",
        "launch_identity",
        "controller_child",
        "stop",
    }:
        unavailable("workflow_waiting_containment")
    for row in receipts:
        _scope(row, controller)
    read_historical_controller_records(
        settings.storage_root,
        intent=by_kind["launch_intent"].payload,
        identity=by_kind["launch_identity"].payload,
        child=by_kind["controller_child"].payload,
        stop=by_kind["stop"].payload,
    )
    reservation_ids = _settlements(session, scope)
    # Explicit v3 branch is mandatory: never use the old fixture short-circuit.
    if not provider_attempt_stopped(session, run, attempt, settings):
        unavailable("workflow_waiting_containment")
    from backend.app.rdagent_workflow_source_history import (
        verify_workflow_source_history,
    )

    verify_workflow_source_history(session, run, source, settings)
    verified = read_analysis_in_session(session, owner_id, analysis.id, settings)
    if (
        verified.result_sha256 != outcome.result_sha256
        or verified.measurement_sha256 != outcome.measurement_sha256
    ):
        unavailable()
    require_native_storage_guard(settings.storage_root)
    return RDStageCheckpoint(
        owner_id=owner_id,
        workflow_id=root.id,
        stage_id=stage.id,
        input_revision_id=stage.input_revision_id,
        input_sha256=config.workflow_inputs.binding.input_sha256,
        definition_sha256=stage.definition_sha256,
        allocation_sha256=stage.allocation_sha256,
        submission_id=receipt.id,
        child_run_id=run.id,
        attempt_id=attempt.id,
        generation=attempt.generation,
        spec_id=receipt.spec_id,
        plan_id=receipt.plan_id,
        start_id=start.id,
        source_id=source.id,
        evaluation_id=evaluation.id,
        factor_version_id=version.id,
        source_sha256=source.source_sha256,
        snapshot_id=publication.id,
        snapshot_manifest_sha256=publication.manifest_sha256,
        analysis_id=analysis.id,
        analysis_result_sha256=outcome.result_sha256,
        analysis_measurement_sha256=outcome.measurement_sha256,
        controller_admission_id=controller.admission_id,
        controller_stop_sha256=by_kind["stop"].payload_sha256,
        process_admission_ids=tuple(sorted(expected, key=str)),
        reservation_ids=reservation_ids,
    )


def read_rd_terminal(factory, settings, *, owner_id, workflow_id, stage_id):
    with historical_session(factory, owner_id, settings) as session:
        return read_rd_terminal_in_session(
            session, owner_id, workflow_id, stage_id, settings
        )
