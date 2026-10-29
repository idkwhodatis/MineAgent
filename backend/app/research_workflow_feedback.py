"""Private two-phase workflow feedback read using the existing release ledger.

No result text escapes before disclosure commits. Revalidation inside publication
uses immutable database commitments, never a nested historical storage lock.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.engine_result_release import disclose, release_bindings, source_key
from backend.app.execution_snapshot_history import historical_session
from backend.app.experiment_services import _identities
from backend.app.models import DataUsageExposure
from backend.app.research_input_storage import require_publication_admission
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowCheckpoint,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_plan_codec import read_guidance_plan
from backend.app.research_workflow_rd_checkpoint import verify_rd_checkpoint
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.feedback import FeedbackProjectionDefinition
from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
from packages.research_workflow.rd_stage import RDStageDefinition
from packages.research_workflow.structured_plan import (
    guidance_stage_schedule as stage_schedule,
)


def fail(code="workflow_feedback_binding_changed"):
    raise DomainError(code, "Bound workflow development feedback is unavailable", 409)


@dataclass(frozen=True)
class FeedbackRead:
    owner_id: UUID
    workflow_id: UUID
    stage_id: UUID
    definition: FeedbackProjectionDefinition
    checkpoint_id: UUID
    checkpoint: RDStageCheckpoint
    verified: object
    exposure_ids: tuple[UUID, UUID]


def _binding(session, owner_id, workflow_id, stage_id):
    require_schema(session)
    root = session.scalar(
        select(ResearchWorkflow).filter_by(owner_id=owner_id, id=workflow_id)
    )
    stage = session.scalar(
        select(ResearchWorkflowStage).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=stage_id
        )
    )
    if (
        root is None
        or stage is None
        or (
            root.owner_id,
            root.id,
            stage.owner_id,
            stage.workflow_id,
            stage.id,
            stage.kind,
            stage.child_run_id,
        )
        != (
            owner_id,
            workflow_id,
            owner_id,
            workflow_id,
            stage_id,
            "experiment_feedback_projection",
            None,
        )
    ):
        fail()
    try:
        plan = read_guidance_plan(session, root.plan_canonical)
        definition = FeedbackProjectionDefinition.model_validate_json(
            stage.definition_canonical
        )
    except (TypeError, ValueError):
        fail()
    schedule = stage_schedule(plan)
    if (
        plan.owner_id != owner_id
        or canonical_bytes(plan).decode() != root.plan_canonical
        or plan.sha256 != root.plan_sha256
        or canonical_bytes(definition).decode() != stage.definition_canonical
        or digest(definition) != stage.definition_sha256
        or not 2 <= stage.ordinal <= len(schedule)
        or schedule[stage.ordinal - 1][0] != "experiment_feedback_projection"
    ):
        fail()
    source = definition.source
    previous = session.scalar(
        select(ResearchWorkflowStage).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, id=source.stage_id
        )
    )
    if (
        previous is None
        or (
            previous.owner_id,
            previous.workflow_id,
            previous.kind,
            previous.child_run_id,
            previous.ordinal,
        )
        != (
            owner_id,
            workflow_id,
            "rd_factor_generation",
            source.child_run_id,
            stage.ordinal - 1,
        )
        or source.workflow_id != workflow_id
        or source.output_kind != "direct_analysis"
    ):
        fail()
    prior_definition = RDStageDefinition.model_validate_json(
        previous.definition_canonical
    )
    if (
        canonical_bytes(prior_definition).decode() != previous.definition_canonical
        or prior_definition.sha256 != previous.definition_sha256
        or prior_definition.plan_sha256 != plan.sha256
        or prior_definition.research_iteration != schedule[stage.ordinal - 1][1]
    ):
        fail()
    row = session.scalar(
        select(ResearchWorkflowCheckpoint).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, stage_id=previous.id
        )
    )
    if row is None:
        fail()
    checkpoint = RDStageCheckpoint.model_validate_json(row.terminal_canonical)
    if (
        (row.owner_id, row.workflow_id, row.stage_id)
        != (owner_id, workflow_id, previous.id)
        or canonical_bytes(checkpoint).decode() != row.terminal_canonical
        or digest(checkpoint) != row.terminal_sha256
        or (
            checkpoint.owner_id,
            checkpoint.workflow_id,
            checkpoint.stage_id,
            checkpoint.child_run_id,
            checkpoint.analysis_id,
            checkpoint.analysis_result_sha256,
            checkpoint.definition_sha256,
        )
        != (
            owner_id,
            workflow_id,
            source.stage_id,
            source.child_run_id,
            source.output_id,
            source.content_sha256,
            previous.definition_sha256,
        )
    ):
        fail()
    return root, stage, plan, definition, row, checkpoint


def _verify_buffer(verified, checkpoint):
    from backend.app.factor_analysis_history import HistoricalDirectAnalysis
    from packages.qlib_adapter.contracts import DirectResult
    from packages.qlib_adapter.objective import DirectObjectiveMeasurement

    if (
        type(verified) is not HistoricalDirectAnalysis
        or type(verified.result) is not DirectResult
        or type(verified.measurement) is not DirectObjectiveMeasurement
    ):
        fail()
    if (
        digest(verified.result) != checkpoint.analysis_result_sha256
        or digest(verified.measurement) != checkpoint.analysis_measurement_sha256
        or verified.measurement.result_sha256 != checkpoint.analysis_result_sha256
    ):
        fail()


def prepare_feedback_read(factory, settings, *, owner_id, workflow_id, stage_id):
    """New stage may use only its immutable initial workflow opt-in policy."""
    return _prepare(
        factory,
        settings,
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        initial_policy=True,
    )


def read_released_feedback(factory, settings, *, owner_id, workflow_id, stage_id):
    """Historical immutable feedback read; NEVER creates an exposure release."""
    return _prepare(
        factory,
        settings,
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        initial_policy=False,
    )


def _prepare(factory, settings, *, owner_id, workflow_id, stage_id, initial_policy):
    """Actual prior-child read then existing disclose OUTSIDE held owner locks."""
    with historical_session(factory, owner_id, settings) as session:
        _, _, plan, definition, row, checkpoint = _binding(
            session, owner_id, workflow_id, stage_id
        )
        _, actual = verify_rd_checkpoint(
            session, owner_id, workflow_id, row.id, settings
        )
        if actual != checkpoint:
            fail()
        checkpoint_id = row.id
    try:
        verified, exposure_ids = disclose(
            factory,
            settings,
            owner_id=owner_id,
            run_id=checkpoint.child_run_id,
            resource_id=checkpoint.analysis_id,
            kind="direct_analysis",
            create_release=initial_policy
            and plan.allow_workflow_development_feedback_release,
        )
    except DomainError as exc:
        if exc.code == "engine_result_release_required":
            fail("workflow_feedback_release_required")
        raise
    if (
        len(exposure_ids) != 2
        or len(set(exposure_ids)) != 2
        or (verified.analysis_id, verified.result_sha256, verified.measurement_sha256)
        != (
            checkpoint.analysis_id,
            checkpoint.analysis_result_sha256,
            checkpoint.analysis_measurement_sha256,
        )
    ):
        fail()
    _verify_buffer(verified, checkpoint)
    return FeedbackRead(
        owner_id,
        workflow_id,
        stage_id,
        definition,
        checkpoint_id,
        checkpoint,
        verified,
        tuple(exposure_ids),
    )


def revalidate_feedback_read(
    session, owner_id, workflow_id, stage_id, settings, read, *, admission
):
    """Existing publication guard, actual immutable binding/ledger only."""
    require_publication_admission(session, settings, admission)
    result = _revalidate(session, owner_id, workflow_id, stage_id, read)
    require_publication_admission(session, settings, admission)
    return result


def revalidate_released_feedback(
    session, owner_id, workflow_id, stage_id, settings, read
):
    """Historical/child resolver under its actual native guard; no new release."""
    from backend.app.factor_native_io import require_native_storage_guard

    require_native_storage_guard(settings.storage_root)
    result = _revalidate(session, owner_id, workflow_id, stage_id, read)
    require_native_storage_guard(settings.storage_root)
    return result


def _revalidate(session, owner_id, workflow_id, stage_id, read):
    if type(read) is not FeedbackRead or (
        read.owner_id,
        read.workflow_id,
        read.stage_id,
    ) != (owner_id, workflow_id, stage_id):
        fail()
    _, _, _, definition, row, checkpoint = _binding(
        session, owner_id, workflow_id, stage_id
    )
    if (
        definition != read.definition
        or row.id != read.checkpoint_id
        or checkpoint != read.checkpoint
    ):
        fail()
    _verify_buffer(read.verified, checkpoint)
    run, bindings = release_bindings(
        session,
        owner_id,
        checkpoint.child_run_id,
        checkpoint.analysis_id,
        "direct_analysis",
        read.verified,
    )
    if run.purpose != "factor_research" or run.engine != "rdagent":
        fail()
    found = []
    for role, dataset, evidence in bindings:
        key = source_key("direct_analysis", checkpoint.analysis_id, role)
        prior = session.scalar(
            select(DataUsageExposure).filter_by(owner_id=owner_id, source_key=key)
        )
        if prior is None:
            fail("workflow_feedback_release_required")
        expected_provenance = {
            **evidence,
            "role": role,
            "dataset_version_id": str(dataset.id),
            "dataset_content_sha256": dataset.content_sha256,
        }
        if (
            prior.owner_id,
            prior.run_id,
            prior.experiment_id,
            prior.split_version_id,
            prior.dataset_version_id,
            prior.source_key,
            prior.start_date,
            prior.end_date,
            prior.purpose,
            prior.identities,
            prior.provenance,
        ) != (
            owner_id,
            run.id,
            run.experiment_id,
            run.split_version_id,
            dataset.id,
            key,
            dataset.start_date,
            dataset.end_date,
            "engine_direct_analysis_release",
            _identities(session, dataset),
            expected_provenance,
        ):
            fail()
        found.append(prior.id)
    if tuple(found) != read.exposure_ids:
        fail()
    return read.verified, read.exposure_ids


def read_released_feedback_in_session(
    session, owner_id, workflow_id, stage_id, settings
):
    """Existing release only, real bytes under the caller's native guard.

    No factory transaction, new exposure, current-head check or advisory lock.
    Missing release is rejected BEFORE historical result bytes are read.
    """
    from backend.app.factor_analysis_history import read_analysis_in_session
    from backend.app.factor_native_io import require_native_storage_guard

    require_native_storage_guard(settings.storage_root)
    _, _, _, definition, row, checkpoint = _binding(
        session, owner_id, workflow_id, stage_id
    )
    ids = []
    for role in ("factor", "label"):
        exposure = session.scalar(
            select(DataUsageExposure).filter_by(
                owner_id=owner_id,
                source_key=source_key("direct_analysis", checkpoint.analysis_id, role),
            )
        )
        if (
            exposure is None
            or exposure.owner_id != owner_id
            or exposure.run_id != checkpoint.child_run_id
        ):
            fail("workflow_feedback_release_required")
        ids.append(exposure.id)
    if len(set(ids)) != 2:
        fail()
    _, actual = verify_rd_checkpoint(session, owner_id, workflow_id, row.id, settings)
    if actual != checkpoint:
        fail()
    verified = read_analysis_in_session(
        session, owner_id, checkpoint.analysis_id, settings
    )
    read = FeedbackRead(
        owner_id,
        workflow_id,
        stage_id,
        definition,
        row.id,
        checkpoint,
        verified,
        tuple(ids),
    )
    revalidate_released_feedback(
        session, owner_id, workflow_id, stage_id, settings, read
    )
    return read
