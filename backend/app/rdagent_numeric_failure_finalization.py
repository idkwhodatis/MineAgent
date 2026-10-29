"""027 no-snapshot feedback from actual protected rejection and upstream calls."""

import json
from uuid import uuid4

from sqlalchemy import select

from backend.app.rdagent_models import (
    FactorEvaluationOutcome,
    FactorUpstreamCall,
    FactorUpstreamCallOutcome,
)
from backend.app.rdagent_numeric_failure_models import FactorNumericFailureFeedback
from backend.app.rdagent_numeric_failure_services import read_candidate_failure
from backend.app.rdagent_operational_broker import _publish
from backend.app.rdagent_operational_evaluation import snapshot_transaction
from backend.app.rdagent_services import (
    _call_members,
    _completed_member_response,
    _scope,
)
from backend.app.research_object_services import read_research_object
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.numeric_failure import RDNumericFailureFeedback
from packages.rdagent_adapter.numeric_failure_codec import parse_failure_feedback
from runtimes.rdagent_operational.rpc import RPCError


def _closed_phase(session, fence, observed, phase, settings):
    calls = list(
        session.scalars(
            select(FactorUpstreamCall).filter_by(
                **_scope(fence),
                start_id=observed.source.start_id,
                phase=phase,
                kind="chat",
            )
        ).all()
    )
    if len(calls) != 1:
        raise RPCError("numeric_failure_upstream_call_missing")
    call = calls[0]
    finished = session.scalar(
        select(FactorUpstreamCallOutcome).filter_by(
            **_scope(fence),
            call_id=call.id,
        )
    )
    members = _call_members(session, fence, call.id)
    if (
        finished is None
        or finished.status != "completed"
        or finished.controller_launch_id != observed.controller_launch.id
        or len(members) != 1
        or finished.member_count != 1
    ):
        raise RPCError("numeric_failure_upstream_call_unclosed")
    _, response = _completed_member_response(session, fence, members[0], settings)
    from backend.app.rdagent_services import _scope_row
    from backend.app.research_broker_models import ResearchModelOperation

    operation = _scope_row(
        session, ResearchModelOperation, members[0].operation_id, fence
    )
    if phase == "critique" and observed.frame.feedback_text not in "\n".join(
        message["content"] for message in operation.request["messages"]
    ):
        raise RPCError("numeric_failure_not_consumed_by_critique")
    return call, response


def retain_numeric_failure_feedback(
    factory,
    fence,
    peer,
    *,
    start_id,
    source_id,
    evaluation_id,
    upstream_feedback,
    settings,
):
    with snapshot_transaction(factory, fence, settings) as session:
        observed = read_candidate_failure(
            session,
            fence,
            peer,
            source_id=source_id,
            evaluation_id=evaluation_id,
            settings=settings,
        )
        if observed is None or observed.source.start_id != start_id:
            raise RPCError("numeric_failure_evidence_missing")
        _, critique = _closed_phase(session, fence, observed, "critique", settings)
        call, response = _closed_phase(
            session, fence, observed, "final_decision", settings
        )
        decision = json.loads(response.content)
        if (
            type(upstream_feedback) is not dict
            or type(decision.get("final_decision")) is not bool
            or decision["final_decision"] != upstream_feedback.get("final_decision")
            or upstream_feedback.get("execution") != observed.frame.feedback_text
            or upstream_feedback.get("code") != critique.content
        ):
            raise RPCError("numeric_failure_actual_feedback_differs")
        document = RDNumericFailureFeedback(
            **_scope(fence),
            start_id=start_id,
            controller_launch_id=observed.controller_launch.id,
            final_decision_call_id=call.id,
            failure=observed.frame,
            code_sha256=observed.source.source_sha256,
            input_sha256=observed.evaluation.input_sha256,
            upstream_feedback=canonical_bytes(upstream_feedback).decode("ascii"),
        )
    raw = canonical_bytes(document)
    object_id = _publish(
        factory,
        fence,
        settings,
        "factor_generation_feedback",
        raw,
        "rd-numeric-failure-feedback:" + str(evaluation_id),
    )
    with snapshot_transaction(factory, fence, settings) as session:
        current = read_candidate_failure(
            session,
            fence,
            peer,
            source_id=source_id,
            evaluation_id=evaluation_id,
            settings=settings,
        )
        if (
            current is None
            or current.frame != document.failure
            or current.controller_launch.id != document.controller_launch_id
        ):
            raise RPCError("numeric_failure_changed_before_commit")
        actual = read_research_object(
            session,
            fence,
            object_id,
            "factor_generation_feedback",
            digest(document),
            settings,
        )
        if parse_failure_feedback(actual) != document:
            raise RPCError("numeric_failure_retained_feedback_differs")
        existing = session.scalar(
            select(FactorNumericFailureFeedback).filter_by(
                **_scope(fence),
                evaluation_id=evaluation_id,
            )
        )
        if existing is not None:
            if (
                existing.feedback_object_id != object_id
                or existing.feedback_sha256 != digest(document)
            ):
                raise RPCError("numeric_failure_feedback_replay_differs")
            return object_id
        result = FactorEvaluationOutcome(
            id=uuid4(),
            **_scope(fence),
            evaluation_id=evaluation_id,
            status="execution_failed",
            snapshot_id=None,
            snapshot_input_sha256=None,
            snapshot_output_sha256=None,
            feedback_object_id=object_id,
            feedback_object_kind="factor_generation_feedback",
            feedback_sha256=digest(document),
        )
        session.add(result)
        session.flush()
        session.add(
            FactorNumericFailureFeedback(
                id=uuid4(),
                **_scope(fence),
                evaluation_id=evaluation_id,
                evaluation_outcome_id=result.id,
                cutoff_execution_id=current.cutoff.id,
                candidate_admission_id=current.frame.candidate_admission_id,
                controller_launch_id=current.controller_launch.id,
                final_decision_call_id=call.id,
                rejection_receipt_id=current.frame.rejection_receipt_id,
                rejection_kind="rejection",
                rejection_sha256=current.frame.rejection_sha256,
                stop_receipt_id=current.frame.stop_receipt_id,
                stop_kind="stop",
                stop_sha256=current.frame.stop_sha256,
                feedback_object_id=object_id,
                feedback_object_kind="factor_generation_feedback",
                feedback_sha256=digest(document),
            )
        )
        session.flush()
    return object_id
