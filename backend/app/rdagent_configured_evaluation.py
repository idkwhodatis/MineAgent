"""Configured complete snapshot→actual protected Qlib measurement callback.

No host native parsing, hardcoded rows or fixture-objective substitution. Partial
external executions require reconciliation rather than a second launch.
"""

from sqlalchemy import select

from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.execution_snapshot_services import (
    publish_execution_snapshot,
    read_execution_snapshot,
)
from backend.app.factor_analysis_models import (
    FactorDirectAnalysisIntent,
    FactorDirectAnalysisOutcome,
)
from backend.app.factor_analysis_results import read_verified_direct_analysis
from backend.app.factor_analysis_services import prepare_direct_analysis_intent
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from backend.app.rdagent_operational_evaluation import snapshot_transaction
from backend.app.rdagent_services import _scope, _scope_row
from backend.app.research_process_models import ResearchProcessAdmission
from packages.factors.contracts import SnapshotInput, digest
from runtimes.rdagent_operational.rpc import RPCError


def read_configured_evaluation(session, fence, peer, evaluation_id, settings):
    require_rd_controller(session, fence, peer)
    evaluation = _scope_row(session, FactorEvaluationStart, evaluation_id, fence)
    publication = session.scalar(
        select(ExecutionSnapshotPublication).filter_by(
            **_scope(fence), evaluation_id=evaluation_id
        )
    )
    if publication is None:
        raise RPCError("configured_snapshot_missing")
    snapshot = read_execution_snapshot(session, fence, publication.id, settings)
    if (
        snapshot.evaluation_id,
        snapshot.source_id,
        snapshot.generation_start_id,
        snapshot.factor_version_id,
        snapshot.input_sha256,
    ) != (
        evaluation.id,
        evaluation.evaluated_source_id,
        evaluation.start_id,
        evaluation.factor_version_id,
        evaluation.input_sha256,
    ):
        raise RPCError("configured_snapshot_binding")
    analysis = session.scalar(
        select(FactorDirectAnalysisIntent).filter_by(
            **_scope(fence), snapshot_id=snapshot.snapshot_id
        )
    )
    if analysis is None:
        raise RPCError("configured_analysis_missing")
    measured = read_verified_direct_analysis(session, fence, analysis.id, settings)
    if measured.snapshot_id != snapshot.snapshot_id:
        raise RPCError("configured_analysis_snapshot_drift")
    return snapshot, SnapshotInput.model_validate(evaluation.snapshot_input), measured


def measured_configured_start(factory, fence, peer, start_id, settings):
    with snapshot_transaction(factory, fence, settings) as session:
        evaluation = session.scalar(
            select(FactorEvaluationStart).filter_by(**_scope(fence), start_id=start_id)
        )
        if evaluation is None:
            raise RPCError("configured_evaluation_missing")
        snapshot, _, measured = read_configured_evaluation(
            session, fence, peer, evaluation.id, settings
        )
        return snapshot, measured.measurement.status == "passed"


def execute_configured_evaluation(
    factory, fence, peer, *, source_id, evaluation_id, settings
):
    def authorize(session, current):
        require_rd_controller(session, current, peer)
        return peer

    with snapshot_transaction(factory, fence, settings) as session:
        authorize(session, fence)
        source = _scope_row(session, FactorGenerationSource, source_id, fence)
        evaluation = _scope_row(session, FactorEvaluationStart, evaluation_id, fence)
        if (
            evaluation.evaluated_source_id,
            evaluation.factor_version_id,
            evaluation.start_id,
        ) != (
            source.id,
            source.factor_version_id,
            source.start_id,
        ) or evaluation.expected_cutoffs != 1:
            raise RPCError("configured_complete_single_cutoff_required")
        existing = session.scalar(
            select(ExecutionSnapshotPublication).filter_by(
                **_scope(fence), evaluation_id=evaluation_id
            )
        )
        if (
            existing is None
            and session.scalar(
                select(FactorCutoffExecution).filter_by(
                    **_scope(fence), evaluation_id=evaluation_id
                )
            )
            is not None
        ):
            raise RPCError("configured_partial_execution_requires_reconciliation")
        pending = (
            existing is not None
            and session.get(ExecutionSnapshotOutcome, existing.id) is None
        )
    if existing is None:
        from backend.app.factor_native_services import publish_completed_candidate
        from workers.research.rdagent_numeric import (
            execute_configured_candidate,
            execute_configured_normalizer,
        )

        admission = execute_configured_candidate(
            factory,
            fence,
            settings,
            evaluation_id=evaluation_id,
            cutoff_ordinal=0,
            authorize_submission=authorize,
        )
        with snapshot_transaction(factory, fence, settings) as session:
            authorize(session, fence)
        native = publish_completed_candidate(factory, fence, admission, settings)
        execute_configured_normalizer(
            factory,
            fence,
            settings,
            publication_id=native,
            authorize_submission=authorize,
        )
    if existing is None or pending:
        with snapshot_transaction(factory, fence, settings) as session:
            authorize(session, fence)
        snapshot_id = publish_execution_snapshot(
            factory, fence, evaluation_id, settings
        )
    else:
        snapshot_id = existing.id
    with snapshot_transaction(factory, fence, settings) as session:
        authorize(session, fence)
        analysis_id = prepare_direct_analysis_intent(
            session, fence, snapshot_id, settings
        )
        outcome = session.get(FactorDirectAnalysisOutcome, analysis_id)
        if (
            outcome is None
            and session.scalar(
                select(ResearchProcessAdmission).filter_by(
                    **_scope(fence),
                    logical_operation_id=analysis_id,
                    role="native_evaluator",
                )
            )
            is not None
        ):
            raise RPCError("configured_partial_analysis_requires_reconciliation")
    if outcome is None:
        from workers.research.analysis_process import execute_analysis_acceptance_unit

        analysis_admission_id = execute_analysis_acceptance_unit(
            factory, fence, analysis_id, settings, authorize_submission=authorize
        )
        from backend.app.factor_analysis_results import persist_direct_analysis_outcome

        with snapshot_transaction(factory, fence, settings) as session:
            authorize(session, fence)
            persist_direct_analysis_outcome(
                session, fence, analysis_id, analysis_admission_id, settings
            )
    with snapshot_transaction(factory, fence, settings) as session:
        snapshot, _, measured = read_configured_evaluation(
            session, fence, peer, evaluation_id, settings
        )
        rows = [row.model_dump(mode="json") for row in snapshot.rows]
        return {
            "schema_version": "mineagent.rdagent-evaluation-frame.v1",
            "source_id": str(snapshot.source_id),
            "evaluation_id": str(snapshot.evaluation_id),
            "snapshot_id": str(snapshot.snapshot_id),
            "factor_version_id": str(snapshot.factor_version_id),
            "rows": rows,
            "rows_sha256": digest(rows),
            "feedback": f"Complete retained execution snapshot {snapshot.snapshot_id}; actual stopped direct Qlib analysis {measured.analysis_id}; objective status={measured.measurement.status}, reason={measured.measurement.reason}. Declared availability is not independently verified PIT; input provision does not prove computation exposure.",
        }
