"""Disabled, transaction-local worker action gates; never a reusable capability.

No listener calls these yet. Observer mutations are flush-only; ACK must follow
owner commit. Model/evaluator checks do not launch I/O, reserve retries, or replace
broker/process admission. Historical receipt replay belongs to the separately
live-controller-authenticated receipt path, never to a closed worker assignment.
"""

from sqlalchemy import select

from backend.app import openevolve_models as m
from backend.app import openevolve_services as domain
from backend.app.openevolve_worker_models import SearchWorkerAssignment
from backend.app.openevolve_worker_services import _closure, _live_worker, _same_peer
from backend.app.research_broker_models import (
    ResearchContextAbort,
    ResearchModelOperation,
    ResearchOperationContext,
)
from backend.app.services import DomainError
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.openevolve_adapter.observer_transport import canonical
from packages.research_runtime.broker_contracts import digest


def _deny():
    raise DomainError(
        "search_worker_action", "Worker action lacks exact open phase authority", 409
    )


def _row(session, model, intent, fence, **criteria):
    row = session.scalar(
        select(model).where(
            model.intent_id == intent.id,
            *(getattr(model, key) == value for key, value in criteria.items()),
        )
    )
    if row is not None and (
        row.epoch_id != intent.epoch_id
        or any(
            getattr(row, key) != value for key, value in domain._scope(fence).items()
        )
    ):
        _deny()
    return row


def _open_worker(session, fence, authorization, grant, profile):
    epoch, intent, ticket, expected, observed = _live_worker(
        session, fence, authorization, grant, profile
    )
    assignment = _row(session, SearchWorkerAssignment, intent, fence)
    if (
        assignment is None
        or assignment.admission_id != epoch.admission_id
        or assignment.ticket_id != ticket.id
        or assignment.expected_identity_id != expected.id
        or not _same_peer(assignment, observed)
        or _closure(session, assignment.id) is not None
        or _row(session, m.SearchWorkerOutcome, intent, fence) is not None
    ):
        _deny()
    return epoch, intent


def _phase(session, fence, intent, admission_id):
    rendered = _row(session, m.SearchRenderedContext, intent, fence)
    proposal = _row(session, m.SearchProposalResponse, intent, fence)
    applied = _row(session, m.SearchProposalSource, intent, fence, stage="applied")
    enforced = _row(session, m.SearchProposalSource, intent, fence, stage="enforced")
    candidate = _row(session, m.SearchCandidateGrant, intent, fence)
    # Refuse broken closure, rather than interpreting partial evidence as a phase.
    if (
        (
            proposal is not None
            and (rendered is None or proposal.rendered_context_id != rendered.id)
        )
        or (
            applied is not None
            and (proposal is None or applied.proposal_id != proposal.id)
        )
        or (
            enforced is not None
            and (applied is None or enforced.proposal_id != proposal.id)
        )
        or (
            candidate is not None
            and (
                enforced is None
                or candidate.proposal_source_id != enforced.id
                or candidate.phase != "mutation"
                or candidate.admission_id != admission_id
            )
        )
    ):
        _deny()
    return rendered, proposal, applied, enforced, candidate


def require_worker_model_operation(
    session,
    fence,
    authorization,
    grant,
    *,
    expected_process_profile,
    context_id,
    operation_id,
    request_sha256,
):
    """Check a preexisting exact logical operation; never create placeholder calls."""
    epoch, intent = _open_worker(
        session, fence, authorization, grant, expected_process_profile
    )
    rendered, proposal, applied, enforced, candidate = _phase(
        session, fence, intent, epoch.admission_id
    )
    if rendered is None or any(
        x is not None for x in (proposal, applied, enforced, candidate)
    ):
        _deny()
    context = domain._owned_row(session, ResearchOperationContext, context_id, fence)
    operation = domain._owned_row(session, ResearchModelOperation, operation_id, fence)
    projection = SearchOperationContext.model_validate_json(canonical(context.context))
    if (
        rendered.context_id != context.id
        or operation.context_id != context.id
        or operation.purpose != "chat"
        or type(operation.ordinal) is not int
        or operation.ordinal != 0
        or operation.state != "open"
        or operation.request_sha256 != rendered.request_sha256
        or request_sha256 != operation.request_sha256
        or context.context_sha256 != digest(projection)
        or (
            projection.owner_id,
            projection.run_id,
            projection.epoch_id,
            projection.iteration_intent_id,
            projection.native_iteration,
            projection.parent_candidate_id,
            projection.parent_source_sha256,
            projection.selection_snapshot_sha256,
            projection.rendered_context_sha256,
            projection.operation_ordinal,
        )
        != (
            fence.owner_id,
            fence.run_id,
            intent.epoch_id,
            intent.id,
            intent.native_iteration,
            intent.parent_candidate_id,
            intent.parent_source_sha256,
            intent.selection_sha256,
            rendered.rendered_sha256,
            0,
        )
        or session.scalar(
            select(ResearchContextAbort).where(
                ResearchContextAbort.context_id == context.id
            )
        )
        is not None
    ):
        _deny()


def require_worker_evaluation(
    session,
    fence,
    authorization,
    grant,
    *,
    expected_process_profile,
    candidate_grant_id,
    evaluation_id,
    logical_ticket_id,
):
    """Mutation-only request check; candidate process admission remains separate."""
    epoch, intent = _open_worker(
        session, fence, authorization, grant, expected_process_profile
    )
    candidate = _phase(session, fence, intent, epoch.admission_id)[4]
    evaluation = domain._owned_row(session, m.SearchEvaluation, evaluation_id, fence)
    if (
        candidate is None
        or candidate.id != candidate_grant_id
        or (
            evaluation.epoch_id,
            evaluation.candidate_id,
            evaluation.source_sha256,
            evaluation.phase,
            evaluation.logical_ticket_id,
        )
        != (
            epoch.id,
            candidate.candidate_id,
            candidate.source_sha256,
            "mutation",
            logical_ticket_id,
        )
    ):
        _deny()
    if (
        session.scalar(
            select(m.SearchObjectiveAcknowledgement).where(
                m.SearchObjectiveAcknowledgement.grant_id == candidate.id
            )
        )
        is not None
        or session.scalar(
            select(m.SearchEvaluationOutcome).where(
                m.SearchEvaluationOutcome.evaluation_id == evaluation.id
            )
        )
        is not None
    ):
        _deny()


def apply_worker_observer_command(
    session,
    fence,
    authorization,
    grant,
    command,
    *,
    event,
    expected_process_profile,
    settings,
):
    """Closed dispatcher over existing domain validators; no caller-supplied handler."""
    from backend.app.openevolve_worker_services import close_worker_assignment
    from packages.openevolve_adapter import host_contracts as c

    types = {
        "rendered_prompt": c.RenderedContextCommand,
        "proposal_response": c.ProposalResponseCommand,
        "proposal_applied": c.ProposalSourceCommand,
        "proposal_enforced": c.ProposalSourceCommand,
        "candidate_intent": c.CandidateIntentCommand,
        "evaluation_complete": c.ObjectiveAckCommand,
        "worker_outcome": c.WorkerOutcomeCommand,
    }
    if type(event) is not str or event not in types:
        _deny()
    command = types[event].model_validate(command.model_dump(mode="python"))
    epoch, intent = _open_worker(
        session, fence, authorization, grant, expected_process_profile
    )
    if command.epoch_id != epoch.id or (
        hasattr(command, "intent_id") and command.intent_id != intent.id
    ):
        _deny()
    rendered, proposal, applied, enforced, candidate = _phase(
        session, fence, intent, epoch.admission_id
    )
    if event == "rendered_prompt":
        if any(
            x is not None for x in (rendered, proposal, applied, enforced, candidate)
        ):
            _deny()
        return domain.register_rendered_context(session, fence, command, settings)
    if event == "proposal_response":
        if rendered is None or proposal is not None:
            _deny()
        return domain.record_proposal_response(session, fence, command, settings)
    if event in ("proposal_applied", "proposal_enforced"):
        stage = "applied" if event == "proposal_applied" else "enforced"
        source = c.ProposalSourceEnvelope.model_validate_json(
            canonical(
                domain._read_evidence(
                    session, fence, command.source, "search_proposal_source", settings
                )
            )
        )
        if (
            proposal is None
            or command.proposal_id != proposal.id
            or source.stage != stage
            or candidate is not None
            or enforced is not None
            or (stage == "applied" and applied is not None)
            or (stage == "enforced" and applied is None)
        ):
            _deny()
        return domain.record_proposal_source(session, fence, command, settings)
    if event == "candidate_intent":
        if (
            enforced is None
            or enforced.evidence != "complete_source"
            or candidate is not None
            or command.phase != "mutation"
            or command.admission_id != epoch.admission_id
        ):
            _deny()
        return domain.register_candidate_intent(session, fence, command, settings)
    if event == "evaluation_complete":
        if candidate is None or command.grant_id != candidate.id:
            _deny()
        if (
            session.scalar(
                select(m.SearchObjectiveAcknowledgement).where(
                    m.SearchObjectiveAcknowledgement.grant_id == candidate.id
                )
            )
            is not None
        ):
            _deny()
        return domain.acknowledge_objective(session, fence, command)
    # Failure may occur at any phase. Domain validation requires the actual
    # failure evidence; successful completion requires exact objective ACK.
    if command.native_iteration != intent.native_iteration:
        _deny()
    if candidate is not None:
        ack = session.scalar(
            select(m.SearchObjectiveAcknowledgement).where(
                m.SearchObjectiveAcknowledgement.grant_id == candidate.id
            )
        )
        if ack is not None and command.objective_ack_id != ack.id:
            _deny()
    outcome = domain.record_worker_outcome(session, fence, command, settings)
    close_worker_assignment(
        session,
        fence,
        authorization,
        grant,
        worker_outcome_id=outcome.id,
        expected_process_profile=expected_process_profile,
    )
    return outcome
