"""Persist authenticated actual-loop observations without certifying evaluation.

No accepted outcome, checkpoint, snapshot or released budget is produced here.
Fresh-only lineage stays within this exact attempt; resume needs separate closure.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.rdagent_boundary_models import (
    FactorGenerationBoundary,
    FactorGenerationObservation,
)
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import (
    FactorGenerationOutcome,
    FactorGenerationSource,
    FactorGenerationStart,
    FactorUpstreamCall,
    FactorUpstreamCallOutcome,
)
from backend.app.rdagent_services import _scope_row, require_research_spec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.rdagent_adapter.host_evidence import ObservedGenerationDecision
from packages.research_runtime.process_contracts import digest

QUALIFICATION = "authenticated_observation_not_execution_verification"


def _error():
    return DomainError(
        "factor_boundary_drift",
        "Authenticated loop observation does not match persisted generation",
        409,
    )


def _scope(fence):
    return {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }


def _matching(row, fence):
    return all(getattr(row, key) == value for key, value in _scope(fence).items())


def require_unobserved_generation(session, fence, start_id):
    # Used only after exact replay checks by generation writers. Once the actual
    # decision is observed, new calls or source publication cannot mutate its past.
    if (
        session.scalar(
            select(FactorGenerationObservation).filter_by(
                **_scope(fence), start_id=start_id
            )
        )
        is not None
    ):
        raise _error()


def _open(session, fence, start):
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=start.id
            )
        )
        is not None
    ):
        raise _error()


def _closed_calls(session, fence, start, launch):
    calls = session.scalars(
        select(FactorUpstreamCall)
        .filter_by(**_scope(fence), start_id=start.id)
        .limit(6001)
    ).all()
    if not 1 <= len(calls) <= 6000 or not any(
        call.phase == "knowledge_update" and call.kind == "embedding" for call in calls
    ):
        raise _error()
    for call in calls:
        outcome = session.scalar(
            select(FactorUpstreamCallOutcome).filter_by(
                **_scope(fence), call_id=call.id
            )
        )
        if (
            outcome is None
            or outcome.status != "completed"
            or outcome.controller_launch_id != launch.id
            or not _matching(outcome, fence)
            or outcome.call_id != call.id
        ):
            raise _error()


def _decision(document):
    try:
        return ObservedGenerationDecision.model_validate(document)
    except ValueError as error:
        raise _error() from error


def record_generation_observation(session, fence, authorization, *, start_id, decision):
    require_neutral_transaction(session, fence)
    launch = require_rd_controller(session, fence, authorization)
    start = _scope_row(session, FactorGenerationStart, start_id, fence, lock=True)
    _, config = require_research_spec(session, fence)
    if not 0 <= start.logical_step < config.max_iterations:
        raise _error()
    decision = _decision(decision)
    document = decision.model_dump(mode="json")
    sha = digest(document)
    if decision.logical_step != start.logical_step:
        raise _error()
    existing = session.scalar(
        select(FactorGenerationObservation).filter_by(
            **_scope(fence), start_id=start.id
        )
    )
    if existing is not None:
        if not _matching(existing, fence) or (
            existing.start_id,
            existing.controller_launch_id,
            existing.logical_step,
            existing.decision,
            existing.decision_sha256,
            existing.qualification,
        ) != (start.id, launch.id, start.logical_step, document, sha, QUALIFICATION):
            raise _error()
        return existing
    _open(session, fence, start)
    _closed_calls(session, fence, start, launch)
    source = session.scalar(
        select(FactorGenerationSource).filter_by(**_scope(fence), start_id=start.id)
    )
    if (
        decision.evaluated_source_id != (source.id if source is not None else None)
        or decision.continuation_source_id != decision.evaluated_source_id
    ):
        raise _error()
    previous = None
    if start.logical_step > 0:
        prior = session.scalar(
            select(FactorGenerationBoundary).filter_by(
                **_scope(fence), logical_step=start.logical_step - 1
            )
        )
        if (
            prior is None
            or prior.boundary != "advanced"
            or prior.next_logical_step != start.logical_step
            or prior.controller_launch_id != launch.id
        ):
            raise _error()
        observation = _scope_row(
            session, FactorGenerationObservation, prior.observation_id, fence
        )
        if (
            observation.controller_launch_id != launch.id
            or observation.logical_step != start.logical_step - 1
            or observation.start_id != prior.start_id
            or observation.qualification != QUALIFICATION
            or digest(observation.decision) != observation.decision_sha256
        ):
            raise _error()
        previous = _decision(observation.decision).adopted_source_id
    if decision.previous_adopted_source_id != previous:
        raise _error()
    row = FactorGenerationObservation(
        id=uuid4(),
        **_scope(fence),
        start_id=start.id,
        controller_launch_id=launch.id,
        logical_step=start.logical_step,
        decision=document,
        decision_sha256=sha,
        qualification=QUALIFICATION,
    )
    session.add(row)
    session.flush()
    return row


def confirm_generation_boundary(
    session, fence, authorization, *, observation_id, boundary, next_logical_step
):
    require_neutral_transaction(session, fence)
    launch = require_rd_controller(session, fence, authorization)
    observation = _scope_row(
        session, FactorGenerationObservation, observation_id, fence, lock=True
    )
    start = _scope_row(
        session, FactorGenerationStart, observation.start_id, fence, lock=True
    )
    _, config = require_research_spec(session, fence)
    if (
        observation.controller_launch_id != launch.id
        or observation.qualification != QUALIFICATION
        or digest(observation.decision) != observation.decision_sha256
        or observation.logical_step != start.logical_step
        or _decision(observation.decision).logical_step != start.logical_step
    ):
        raise _error()
    if boundary == "advanced":
        if (
            type(next_logical_step) is not int
            or next_logical_step != start.logical_step + 1
            or next_logical_step >= config.max_iterations
        ):
            raise _error()
    elif boundary != "returned" or next_logical_step is not None:
        raise _error()
    existing = session.scalar(
        select(FactorGenerationBoundary).filter_by(
            **_scope(fence), observation_id=observation.id
        )
    )
    if existing is not None:
        if not _matching(existing, fence) or (
            existing.start_id,
            existing.controller_launch_id,
            existing.logical_step,
            existing.boundary,
            existing.next_logical_step,
        ) != (start.id, launch.id, start.logical_step, boundary, next_logical_step):
            raise _error()
        return existing
    _open(session, fence, start)
    _closed_calls(session, fence, start, launch)
    row = FactorGenerationBoundary(
        id=uuid4(),
        **_scope(fence),
        start_id=start.id,
        observation_id=observation.id,
        controller_launch_id=launch.id,
        logical_step=start.logical_step,
        boundary=boundary,
        next_logical_step=next_logical_step,
    )
    session.add(row)
    session.flush()
    return row
