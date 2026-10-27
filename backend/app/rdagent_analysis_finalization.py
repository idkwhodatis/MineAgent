"""022 finalization from actual stopped declared analysis, never fixture objective."""

from sqlalchemy import select

from backend.app.factor_analysis_models import FactorDirectAnalysisIntent
from backend.app.factor_analysis_results import (
    analysis_feedback_fields,
    read_verified_direct_analysis,
)
from backend.app.rdagent_configured_evaluation import read_configured_evaluation
from backend.app.rdagent_operational_finalization import (
    _finish_generation,
    _retain_feedback,
)
from backend.app.rdagent_services import _scope
from packages.rdagent_adapter.analysis_feedback import RDAnalysisFeedback
from packages.rdagent_adapter.analysis_object_codec import parse_document
from runtimes.rdagent_operational.rpc import RPCError


def _snapshot(session, fence, peer, evaluation_id, settings):
    snapshot, inp, measured = read_configured_evaluation(
        session, fence, peer, evaluation_id, settings
    )
    return snapshot, inp, measured.measurement.status == "passed"


def _fields(session, fence, snapshot, settings):
    intent = session.scalar(
        select(FactorDirectAnalysisIntent).filter_by(
            **_scope(fence), snapshot_id=snapshot.snapshot_id
        )
    )
    if intent is None:
        raise RPCError("analysis_feedback_missing")
    result = read_verified_direct_analysis(session, fence, intent.id, settings)
    if result.snapshot_id != snapshot.snapshot_id:
        raise RPCError("analysis_feedback_snapshot_drift")
    return analysis_feedback_fields(result)


def retain_analysis_feedback(*args, **kwargs):
    return _retain_feedback(
        *args,
        **kwargs,
        read_snapshot=_snapshot,
        feedback_type=RDAnalysisFeedback,
        feedback_fields=_fields,
    )


def _verify_fields(session, fence, snapshot, feedback, settings):
    expected = _fields(session, fence, snapshot, settings)
    if type(feedback) is not RDAnalysisFeedback or any(
        getattr(feedback, key) != value for key, value in expected.items()
    ):
        raise RPCError("analysis_feedback_measurement_drift")


def finish_analysis_generation(*args, **kwargs):
    return _finish_generation(
        *args,
        **kwargs,
        read_snapshot=_snapshot,
        feedback_parser=parse_document,
        feedback_validate=_verify_fields,
        feedback_passed=lambda doc: (
            type(doc) is RDAnalysisFeedback and doc.objective_status == "passed"
        ),
    )
