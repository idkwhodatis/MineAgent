"""Actual retained snapshot/feedback/adoption finalization under live controller fence."""

import json
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.rdagent_boundary_services import (
    confirm_generation_boundary,
    record_generation_observation,
)
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import (
    FactorEvaluationStart,
    FactorGenerationOutcome,
    FactorGenerationSource,
    FactorGenerationStart,
    FactorResponseAssembly,
    FactorUpstreamCall,
)
from backend.app.rdagent_operational_broker import _publish
from backend.app.rdagent_operational_evaluation import (
    read_evaluation_snapshot,
    snapshot_transaction,
)
from backend.app.rdagent_services import (
    _call_members,
    _completed_member_response,
    _scope,
    _scope_row,
)
from backend.app.research_broker_models import ResearchBudgetReservation
from backend.app.research_broker_services import settle_budget
from backend.app.research_object_models import (
    ResearchObject,
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import read_research_object
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.execution_outcomes import (
    DeclaredWindow,
    RDExecutionAdoption,
    RDExecutionFeedback,
    RDExecutionProposal,
    parse_document,
)
from packages.rdagent_adapter.host_evidence import ObservedGenerationDecision
from packages.research_runtime.budget_contracts import BudgetVector
from runtimes.rdagent_operational.rpc import RPCError


def _common(fence, source, snapshot):
    return {
        **_scope(fence),
        "start_id": source.start_id,
        "source_id": source.id,
        "evaluation_id": snapshot.evaluation_id,
        "snapshot_id": snapshot.snapshot_id,
        "factor_version_id": snapshot.factor_version_id,
        "code_sha256": source.source_sha256,
        "input_sha256": snapshot.input_sha256,
        "output_sha256": snapshot.output_sha256,
        "manifest_sha256": snapshot.manifest_sha256,
    }


def _feedback_key(evaluation_id):
    return "rd-execution-feedback:" + str(evaluation_id)


def _published_feedback(session, fence, evaluation_id, settings, *, parser=None):
    held = session.scalar(
        select(ResearchBudgetReservation).filter_by(
            **_scope(fence),
            purpose="object_storage",
            logical_key=_feedback_key(evaluation_id),
        )
    )
    if held is None:
        raise RPCError("actual_feedback_missing")
    publication = session.scalar(
        select(ResearchObjectPublication).filter_by(
            **_scope(fence), budget_reservation_id=held.id
        )
    )
    outcome = (
        session.get(ResearchObjectOutcome, publication.id)
        if publication is not None
        else None
    )
    obj = (
        session.get(ResearchObject, outcome.object_id) if outcome is not None else None
    )
    if obj is None or obj.object_kind != "factor_generation_feedback":
        raise RPCError("actual_feedback_unpublished")
    raw = read_research_object(
        session, fence, obj.id, obj.object_kind, obj.sha256, settings
    )
    return obj, (parser or parse_document)(obj.object_kind, raw)


def retain_feedback(*args, **kwargs):
    return _retain_feedback(*args, **kwargs)


def _retain_feedback(
    factory,
    fence,
    peer,
    *,
    start_id,
    source_id,
    evaluation_id,
    upstream_feedback,
    settings,
    read_snapshot=None,
    feedback_type=None,
    feedback_fields=None,
):
    reader = read_snapshot or read_evaluation_snapshot
    document_type = feedback_type or RDExecutionFeedback
    if (
        type(upstream_feedback) is not dict
        or set(upstream_feedback)
        != {"execution", "return_checking", "code", "final_decision"}
        or type(upstream_feedback["final_decision"]) is not bool
    ):
        raise RPCError("actual_upstream_feedback_shape")
    if any(
        type(upstream_feedback[k]) not in {str, type(None)}
        for k in ("execution", "return_checking", "code")
    ):
        raise RPCError("actual_upstream_feedback_text")
    raw_feedback = canonical_bytes(upstream_feedback)
    if not 1 <= len(raw_feedback) <= 32768:
        raise RPCError("actual_upstream_feedback_bound")
    with snapshot_transaction(factory, fence, settings) as session:
        source = _scope_row(session, FactorGenerationSource, source_id, fence)
        if source.start_id != start_id:
            raise RPCError("source_step")
        snapshot, inp, objective = reader(session, fence, peer, evaluation_id, settings)
        if snapshot.source_id != source_id or snapshot.generation_start_id != start_id:
            raise RPCError("actual_feedback_scope")
        calls = list(
            session.scalars(
                select(FactorUpstreamCall).filter_by(
                    **_scope(fence),
                    start_id=start_id,
                    phase="final_decision",
                    kind="chat",
                )
            )
        )
        if len(calls) != 1:
            raise RPCError("actual_final_decision_missing")
        members = _call_members(session, fence, calls[0].id)
        if len(members) != 1:
            raise RPCError("initial_final_decision_chain")
        _, response = _completed_member_response(session, fence, members[0], settings)
        decision = json.loads(response.content)
        if (
            type(decision.get("final_decision")) is not bool
            or decision["final_decision"] != upstream_feedback["final_decision"]
        ):
            raise RPCError("retained_final_decision_differs")
        doc = document_type(
            **_common(fence, source, snapshot),
            upstream_feedback=raw_feedback.decode("utf8"),
            row_count=len(snapshot.rows),
            missing_count=sum(r.value is None for r in snapshot.rows),
            declared_window=DeclaredWindow(
                start=inp.window.start, end=inp.window.end, complete=True
            ),
            **(
                feedback_fields(session, fence, snapshot, settings)
                if feedback_fields is not None
                else {"objective_passed": objective}
            ),
        )
    return _publish(
        factory,
        fence,
        settings,
        "factor_generation_feedback",
        canonical_bytes(doc),
        _feedback_key(evaluation_id),
    )


def finish_generation(factory, fence, peer, *, body, settings):
    return _finish_generation(factory, fence, peer, body=body, settings=settings)


def _finish_generation(
    factory,
    fence,
    peer,
    *,
    body,
    settings,
    read_snapshot=None,
    feedback_parser=None,
    feedback_passed=None,
    feedback_validate=None,
):
    reader = read_snapshot or read_evaluation_snapshot
    passed = feedback_passed or (lambda value: value.objective_passed)
    from backend.app.execution_snapshot_models import FactorExecutionEvaluationOutcome

    start_id = UUID(body["start_id"])
    decision = ObservedGenerationDecision.model_validate_json(
        json.dumps(
            {
                "schema_version": "mineagent.rdagent-adoption-observation.v1",
                **{
                    k: body[k]
                    for k in (
                        "logical_step",
                        "evaluated_source_id",
                        "previous_adopted_source_id",
                        "adopted_source_id",
                        "continuation_source_id",
                        "adopted_new",
                    )
                },
            }
        )
    )
    if (
        decision.logical_step != 0
        or not decision.adopted_new
        or decision.previous_adopted_source_id is not None
        or body["boundary"] != "returned"
        or body["next_logical_step"] is not None
    ):
        raise RPCError("first_actual_returned_boundary")
    with snapshot_transaction(factory, fence, settings) as session:
        require_rd_controller(session, fence, peer)
        source = _scope_row(
            session, FactorGenerationSource, decision.evaluated_source_id, fence
        )
        if source.start_id != start_id:
            raise RPCError("returned_source_step")
        evaluation = session.scalar(
            select(FactorEvaluationStart).filter_by(**_scope(fence), start_id=start_id)
        )
        if evaluation is None:
            raise RPCError("returned_evaluation_missing")
        snapshot, _, objective = reader(session, fence, peer, evaluation.id, settings)
        feedback_obj, feedback = _published_feedback(
            session, fence, evaluation.id, settings, parser=feedback_parser
        )
        if feedback_validate is not None:
            feedback_validate(session, fence, snapshot, feedback, settings)
        common = _common(fence, source, snapshot)
        if (
            not objective
            or not passed(feedback)
            or json.loads(feedback.upstream_feedback)["final_decision"] is not True
            or any(getattr(feedback, k) != v for k, v in common.items())
        ):
            raise RPCError("actual_acceptance_not_proven")
        observation = record_generation_observation(
            session, fence, peer, start_id=start_id, decision=decision
        )
        boundary = confirm_generation_boundary(
            session,
            fence,
            peer,
            observation_id=observation.id,
            boundary="returned",
            next_logical_step=None,
        )
        assembly = _scope_row(
            session, FactorResponseAssembly, source.assembly_id, fence
        )
        proposal = RDExecutionProposal(
            **common,
            source_assembly_id=assembly.id,
            source_assembly_sha256=assembly.object_sha256,
        )
        actor = (observation.id, boundary.id, boundary.controller_launch_id)
        feedback_id, feedback_sha = feedback_obj.id, feedback_obj.sha256
    proposal_id = _publish(
        factory,
        fence,
        settings,
        "factor_generation_proposal",
        canonical_bytes(proposal),
        "rd-execution-proposal:" + str(start_id),
    )
    adoption = RDExecutionAdoption(
        **common,
        observation_id=actor[0],
        boundary_id=actor[1],
        controller_launch_id=actor[2],
        proposal_object_id=proposal_id,
        proposal_sha256=proposal.sha256,
        feedback_object_id=feedback_id,
        feedback_sha256=feedback_sha,
        adopted_source_id=common["source_id"],
    )
    adoption_id = _publish(
        factory,
        fence,
        settings,
        "factor_adoption_receipt",
        canonical_bytes(adoption),
        "rd-execution-adoption:" + str(start_id),
    )
    with snapshot_transaction(factory, fence, settings) as session:
        require_rd_controller(session, fence, peer)
        snapshot, _, objective = reader(
            session, fence, peer, common["evaluation_id"], settings
        )
        current_obj, current_feedback = _published_feedback(
            session, fence, common["evaluation_id"], settings, parser=feedback_parser
        )
        if feedback_validate is not None:
            feedback_validate(session, fence, snapshot, current_feedback, settings)
        if (
            not objective
            or current_obj.id != feedback_id
            or current_feedback != feedback
        ):
            raise RPCError("final_snapshot_or_feedback_drift")
        # Typed proposal/adoption bytes are reread, not trusted from prior return.
        for identity, kind, sha, raw in (
            (
                proposal_id,
                "factor_generation_proposal",
                proposal.sha256,
                canonical_bytes(proposal),
            ),
            (
                adoption_id,
                "factor_adoption_receipt",
                adoption.sha256,
                canonical_bytes(adoption),
            ),
        ):
            if (
                read_research_object(session, fence, identity, kind, sha, settings)
                != raw
            ):
                raise RPCError("final_artifact_drift")
        if (
            session.scalar(
                select(FactorGenerationOutcome).filter_by(
                    **_scope(fence), start_id=start_id
                )
            )
            is not None
        ):
            raise RPCError("generation_already_final")
        result = FactorExecutionEvaluationOutcome(
            id=uuid4(),
            **_scope(fence),
            evaluation_id=common["evaluation_id"],
            generation_start_id=start_id,
            controller_launch_id=actor[2],
            boundary_id=actor[1],
            snapshot_id=snapshot.snapshot_id,
            status="valid",
            snapshot_input_sha256=snapshot.input_sha256,
            snapshot_output_sha256=snapshot.output_sha256,
            snapshot_manifest_sha256=snapshot.manifest_sha256,
            feedback_object_id=feedback_id,
            feedback_object_kind="factor_generation_feedback",
            feedback_sha256=feedback_sha,
        )
        session.add(result)
        start = _scope_row(session, FactorGenerationStart, start_id, fence, lock=True)
        settle_budget(
            session,
            fence,
            start.mutation_reservation_id,
            charge=BudgetVector(mutation_slots=1),
            outcome="conservative_unknown",
        )
        outcome = FactorGenerationOutcome(
            id=uuid4(),
            **_scope(fence),
            start_id=start_id,
            logical_step=0,
            outcome="accepted",
            factor_version_id=common["factor_version_id"],
            proposal_object_id=proposal_id,
            feedback_object_id=feedback_id,
            error_code=None,
            evaluated_source_id=common["source_id"],
            adopted_source_id=common["source_id"],
            adopted_factor_version_id=common["factor_version_id"],
            continuation_source_id=common["source_id"],
            continuation_factor_version_id=common["factor_version_id"],
            adoption_object_id=adoption_id,
            adoption_object_kind="factor_adoption_receipt",
            adoption_sha256=adoption.sha256,
        )
        session.add(outcome)
        session.flush()
        return outcome.id
