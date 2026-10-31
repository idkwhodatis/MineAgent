"""Explicit027/028 candidate rejection resolution; never infer failure from missing output."""

from dataclasses import dataclass

from sqlalchemy import select, text

from backend.app.numeric_rejection_services import (
    read_numeric_rejection,
    require_numeric_rejection_schema,
)
from backend.app.rdagent_configured_inputs import resolve_candidate_inputs
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from backend.app.rdagent_services import _scope, _scope_row, require_research_spec
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_process_models import ResearchProcessReceipt
from packages.rdagent_adapter.numeric_failure import RDNumericFailureFrame
from runtimes.rdagent_operational.rpc import RPCError


@dataclass(frozen=True)
class ResolvedNumericFailure:
    frame: RDNumericFailureFrame
    source: object
    evaluation: object
    cutoff: object
    controller_launch: object


def require_failure_profile(session, fence, peer):
    require_numeric_rejection_schema(session, fence)
    launch = require_rd_controller(session, fence, peer)
    _, config = require_research_spec(session, fence)
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig

    if type(config) is ProviderFactorResearchConfig:
        if revisions not in (
            ["028_oe_structured_search"],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
            ["032b_oe_append_run_state"],
        ):
            raise RPCError("numeric_failure_configured_profile_required")
    elif type(config) is WorkflowFactorResearchConfig:
        if revisions not in (
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
            ["032b_oe_append_run_state"],
        ):
            raise RPCError("numeric_failure_configured_profile_required")
        from backend.app.models import ResearchRun
        from backend.app.rdagent_workflow_boundary import workflow_boundary_id

        run = session.get(ResearchRun, fence.run_id)
        if run is None or (run.id, run.owner_id) != (fence.run_id, fence.owner_id):
            raise RPCError("numeric_failure_workflow_binding")
        try:
            if workflow_boundary_id(session, run, revisions) is None:
                raise ValueError("Actual immutable workflow submission required")
        except (TypeError, ValueError, AttributeError):
            raise RPCError("numeric_failure_workflow_binding") from None
    else:
        raise RPCError("numeric_failure_configured_profile_required")
    return launch


def read_candidate_failure(session, fence, peer, *, source_id, evaluation_id, settings):
    """Return None only for no recorded rejection; never a failed evaluation claim.

    Once a rejection membership exists, every missing/corrupt/uncharged link is an
    error. Caller may not convert such errors into a model-facing failure frame.
    """
    launch = require_failure_profile(session, fence, peer)
    source = _scope_row(session, FactorGenerationSource, source_id, fence)
    evaluation = _scope_row(
        session, FactorEvaluationStart, evaluation_id, fence, lock=True
    )
    if (
        evaluation.evaluated_source_id,
        evaluation.factor_version_id,
        evaluation.start_id,
        evaluation.expected_cutoffs,
    ) != (source.id, source.factor_version_id, source.start_id, 1):
        raise RPCError("numeric_failure_evaluation_binding")
    cutoffs = list(
        session.scalars(
            select(FactorCutoffExecution).filter_by(
                **_scope(fence),
                evaluation_id=evaluation_id,
            )
        ).all()
    )
    if not cutoffs:
        return None
    if len(cutoffs) != 1:
        raise RPCError("numeric_failure_cutoff_count")
    cutoff = cutoffs[0]
    if (
        cutoff.evaluated_source_id,
        cutoff.factor_version_id,
        cutoff.start_id,
        cutoff.cutoff_ordinal,
    ) != (source.id, source.factor_version_id, source.start_id, 0):
        raise RPCError("numeric_failure_cutoff_binding")
    recorded = session.scalar(
        select(ResearchProcessReceipt).filter_by(
            **_scope(fence),
            admission_id=cutoff.candidate_admission_id,
            kind="rejection",
        )
    )
    if recorded is None:
        return None
    proof = read_numeric_rejection(
        session,
        fence,
        cutoff.candidate_admission_id,
        storage_root=settings.storage_root,
    )
    resolved = resolve_candidate_inputs(
        session, fence, evaluation_id=evaluation_id, cutoff_ordinal=0, settings=settings
    )
    if (
        proof.admission.logical_operation_id != cutoff.id
        or proof.admission.role != "factor_cutoff_candidate"
        or proof.inputs != resolved.binding
        or cutoff.projection != resolved.prefix.model_dump(mode="json")
        or cutoff.projection_sha256 != resolved.prefix.sha256
        or recorded.id != proof.rejection_receipt_id
        or recorded.payload_sha256 != proof.rejection_receipt_hash
    ):
        raise RPCError("numeric_failure_exact_source_inputs")
    held = _scope_row(
        session, ResearchBudgetReservation, proof.admission.reservation_id, fence
    )
    charged = session.scalar(
        select(ResearchBudgetSettlement).filter_by(
            **_scope(fence),
            reservation_id=held.id,
        )
    )
    if (
        held.purpose != "process_unit"
        or held.state != "settled"
        or charged is None
        or charged.outcome != "conservative_unknown"
        or charged.charge != held.amounts
    ):
        raise RPCError("numeric_failure_complete_charge_required")
    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from backend.app.factor_native_models import NativeHDFPublication

    if (
        session.scalar(
            select(ExecutionSnapshotPublication).filter_by(
                **_scope(fence),
                evaluation_id=evaluation_id,
            )
        )
        is not None
        or session.scalar(
            select(NativeHDFPublication).filter_by(
                **_scope(fence),
                admission_id=cutoff.candidate_admission_id,
            )
        )
        is not None
    ):
        raise RPCError("numeric_failure_cannot_alias_numeric_success")
    # Recheck the actual same controller and current lease after physical reads.
    current = require_failure_profile(session, fence, peer)
    if current.id != launch.id:
        raise RPCError("numeric_failure_actor_changed")
    return ResolvedNumericFailure(
        frame=RDNumericFailureFrame(
            source_id=source.id,
            evaluation_id=evaluation.id,
            factor_version_id=source.factor_version_id,
            cutoff_execution_id=cutoff.id,
            candidate_admission_id=cutoff.candidate_admission_id,
            rejection_receipt_id=proof.rejection_receipt_id,
            rejection_sha256=proof.rejection_receipt_hash,
            stop_receipt_id=proof.stop_receipt_id,
            stop_sha256=proof.stop_receipt_hash,
            outcome=proof.outcome,
            child_exit_code=proof.child_exit_code,
        ),
        source=source,
        evaluation=evaluation,
        cutoff=cutoff,
        controller_launch=launch,
    )
