"""022 finalization from actual stopped declared analysis, never fixture objective."""

from backend.app.factor_analysis_results import analysis_feedback_fields
from backend.app.rdagent_configured_evaluation import read_configured_evaluation
from backend.app.rdagent_operational_finalization import (
    _FinalizationEvidence,
    _finish_generation,
    _retain_feedback,
)
from packages.rdagent_adapter.analysis_feedback import RDAnalysisFeedback
from packages.rdagent_adapter.analysis_object_codec import parse_document
from runtimes.rdagent_operational.rpc import RPCError


def _snapshot(session, fence, peer, evaluation_id, settings):
    snapshot, inp, measured = read_configured_evaluation(
        session, fence, peer, evaluation_id, settings
    )
    # Within this one fenced transaction, use the exact typed result already
    # measured against the returned snapshot. No evidence crosses transactions.
    return _FinalizationEvidence(
        snapshot,
        inp,
        measured.measurement.status == "passed",
        analysis_feedback_fields(measured),
    )


def retain_analysis_feedback(*args, **kwargs):
    return _retain_feedback(
        *args,
        **kwargs,
        read_snapshot=_snapshot,
        feedback_type=RDAnalysisFeedback,
    )


def _verify_fields(feedback, expected):
    if (
        type(feedback) is not RDAnalysisFeedback
        or expected is None
        or any(getattr(feedback, key) != value for key, value in expected.items())
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
