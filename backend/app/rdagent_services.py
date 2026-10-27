"""Fail-closed RD domain persistence helpers; no enabled dispatcher or submission.

Activation must supply immutable/deferred DB closure and the RD neutral fence
branch. No helper commits, reconnects, launches upstream, or executes source.
"""

from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.models import ResearchRun
from backend.app.rdagent_models import FactorResearchAttemptBinding, FactorResearchSpec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.research_runtime.fences import AttemptFence


def require_research_spec(
    session, fence: AttemptFence
) -> tuple[FactorResearchSpec, FactorResearchConfig]:
    require_neutral_transaction(session, fence)
    run = session.get(ResearchRun, fence.run_id)
    if (
        run is None
        or run.owner_id != fence.owner_id
        or (run.purpose, run.engine) != ("factor_research", "rdagent")
    ):
        raise DomainError(
            "factor_research_scope_invalid",
            "A current RD-Agent research run is required",
            409,
        )
    spec = session.scalar(
        select(FactorResearchSpec).where(
            FactorResearchSpec.owner_id == fence.owner_id,
            FactorResearchSpec.run_id == fence.run_id,
        )
    )
    if spec is None:
        raise DomainError(
            "factor_research_spec_missing", "Research specification is unavailable", 409
        )
    from packages.rdagent_adapter.provider_config import parse_research_config

    config = parse_research_config(spec.config)
    if (
        config.sha256 != spec.config_sha256
        or config.input.sha256 != spec.input_sha256
        or run.config != config.model_dump(mode="json")
    ):
        raise DomainError(
            "factor_research_spec_changed",
            "Research specification identity differs",
            409,
        )
    if config.schema_version == "mineagent.factor-research.v2":
        from backend.app.rdagent_steer_services import verify_steer_snapshot

        verify_steer_snapshot(session, fence.owner_id, config.steer_snapshot)
    return spec, config


def bind_research_attempt(
    session, fence: AttemptFence, *, resumed_checkpoint_id: UUID | None = None
):
    spec, _ = require_research_spec(session, fence)
    # Resume needs a separately reviewed closure reader; do not enable arbitrary
    # checkpoint IDs merely because an owner/run FK would permit them.
    if resumed_checkpoint_id is not None:
        raise DomainError(
            "factor_resume_not_enabled",
            "Research checkpoint continuation is not yet enabled",
            409,
        )
    old = session.scalar(
        select(FactorResearchAttemptBinding).where(
            FactorResearchAttemptBinding.attempt_id == fence.attempt_id
        )
    )
    if old is not None:
        if (
            old.owner_id,
            old.run_id,
            old.generation,
            old.spec_id,
            old.resumed_checkpoint_id,
        ) != (fence.owner_id, fence.run_id, fence.generation, spec.id, None):
            raise DomainError(
                "factor_attempt_binding_changed",
                "Research attempt binding differs",
                409,
            )
        return old
    row = FactorResearchAttemptBinding(
        id=uuid4(),
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        spec_id=spec.id,
        resumed_checkpoint_id=None,
    )
    session.add(row)
    session.flush()
    return row


def _scope(fence):
    return {
        "owner_id": fence.owner_id,
        "run_id": fence.run_id,
        "attempt_id": fence.attempt_id,
        "generation": fence.generation,
    }


def _scope_row(session, model, identity, fence, *, lock=False):
    query = select(model).filter_by(id=identity, **_scope(fence))
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise DomainError(
            "factor_evidence_scope_invalid",
            "Exact current-attempt evidence required",
            409,
        )
    return row


def begin_generation(
    session,
    fence: AttemptFence,
    *,
    logical_step: int,
    context_id: UUID,
    mutation_reservation_id: UUID,
    parent_factor_version_id: UUID | None = None,
    seeded_submission_id: UUID | None = None,
):
    """Consume already-host-reserved mutation authority; never fabricate a grant.

    RD context codec and engine_mutation reservation purpose are deliberately
    unsupported by the current neutral activation. This helper remains closed.
    """
    from backend.app.rdagent_models import (
        FactorGenerationOutcome,
        FactorGenerationStart,
    )
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchOperationContext,
    )
    from packages.factors.contracts import digest
    from packages.research_runtime.budget_contracts import BudgetVector

    spec, config = require_research_spec(session, fence)
    binding = session.scalar(
        select(FactorResearchAttemptBinding).filter_by(**_scope(fence))
    )
    if (
        binding is None
        or binding.spec_id != spec.id
        or binding.resumed_checkpoint_id is not None
    ):
        raise DomainError(
            "factor_attempt_not_bound", "Fresh exact attempt binding is required", 409
        )
    if type(logical_step) is not int or not 0 <= logical_step < config.max_iterations:
        raise DomainError(
            "factor_step_invalid", "Logical step exceeds frozen configuration", 409
        )
    if seeded_submission_id is not None:
        from backend.app.rdagent_seed_services import require_initial_seed

        if logical_step != 0:
            raise DomainError(
                "factor_seed_not_initial",
                "Seed declaration applies only to the first generation",
                409,
            )
        require_initial_seed(
            session,
            fence,
            submission_id=seeded_submission_id,
            parent_factor_version_id=parent_factor_version_id,
        )
    # Serialize all starts under the immutable spec row (not a local counter).
    session.scalar(
        select(FactorResearchSpec)
        .where(FactorResearchSpec.id == spec.id)
        .with_for_update()
    )
    existing = session.scalar(
        select(FactorGenerationStart).filter_by(
            **_scope(fence), logical_step=logical_step, task_index=0
        )
    )
    if existing is not None:
        if (
            existing.context_id,
            existing.mutation_reservation_id,
            existing.parent_factor_version_id,
        ) != (context_id, mutation_reservation_id, parent_factor_version_id):
            raise DomainError(
                "factor_step_drift", "Generation start identity changed", 409
            )
        return existing
    if (
        session.scalar(
            select(FactorGenerationStart).where(
                FactorGenerationStart.mutation_reservation_id == mutation_reservation_id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_mutation_grant_reused",
            "Mutation reservation is already bound to another start",
            409,
        )
    previous = session.scalars(
        select(FactorGenerationStart)
        .filter_by(**_scope(fence))
        .order_by(FactorGenerationStart.logical_step)
    ).all()
    if [row.logical_step for row in previous] != list(range(logical_step)):
        raise DomainError(
            "factor_step_order", "Generation starts must be contiguous", 409
        )
    if previous:
        if seeded_submission_id is not None:
            raise DomainError(
                "factor_seed_not_initial",
                "Seed declaration applies only to the first generation",
                409,
            )
        outcome = session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=previous[-1].id
            )
        )
        if (
            outcome is None
            or outcome.outcome in {"accepted", "cancelled"}
            or outcome.continuation_factor_version_id != parent_factor_version_id
        ):
            raise DomainError(
                "factor_step_predecessor",
                "Previous completed nonterminal generation required",
                409,
            )
    elif seeded_submission_id is not None:
        # The same persisted receipt was checked before exact replay above.
        pass
    elif parent_factor_version_id is not None:
        raise DomainError(
            "factor_initial_parent", "Fresh research has no parent implementation", 409
        )
    context = _scope_row(session, ResearchOperationContext, context_id, fence)
    expected = {
        "schema_version": "mineagent.rdagent-context.v1",
        "spec_sha256": spec.config_sha256,
        "input_sha256": spec.input_sha256,
        "logical_step": logical_step,
        "task_index": 0,
    }
    if context.context != expected or context.context_sha256 != digest(expected):
        raise DomainError(
            "factor_context_drift",
            "RD context does not match immutable generation",
            409,
        )
    reservation = _scope_row(
        session, ResearchBudgetReservation, mutation_reservation_id, fence
    )
    if (
        reservation.purpose != "engine_mutation"
        or reservation.state != "reserved"
        or reservation.amounts != BudgetVector(mutation_slots=1).model_dump()
    ):
        raise DomainError(
            "factor_mutation_grant_invalid",
            "Exact reserved mutation authority required",
            409,
        )
    row = FactorGenerationStart(
        id=uuid4(),
        **_scope(fence),
        spec_id=spec.id,
        logical_step=logical_step,
        task_index=0,
        context_id=context_id,
        mutation_reservation_id=mutation_reservation_id,
        parent_factor_version_id=parent_factor_version_id,
    )
    session.add(row)
    session.flush()
    return row


def open_upstream_call(
    session,
    fence: AttemptFence,
    *,
    start_id: UUID,
    call_id: UUID,
    phase: str,
    kind: str,
):
    """Host-selected call ID makes retry idempotent; host assigns ordinal under lock."""
    from backend.app.rdagent_models import (
        FactorGenerationOutcome,
        FactorGenerationStart,
        FactorUpstreamCall,
    )

    require_research_spec(session, fence)
    start = _scope_row(session, FactorGenerationStart, start_id, fence, lock=True)
    if (
        phase
        not in {
            "generation",
            "output_format",
            "critique",
            "final_decision",
            "knowledge_query",
            "knowledge_update",
        }
        or kind not in {"chat", "embedding"}
        or (
            kind == "embedding" and phase not in {"knowledge_query", "knowledge_update"}
        )
    ):
        raise DomainError(
            "factor_model_phase_invalid", "Unknown model phase or operation", 409
        )
    existing = session.get(FactorUpstreamCall, call_id)
    if existing is not None:
        if any(
            getattr(existing, key) != value for key, value in _scope(fence).items()
        ) or (
            existing.start_id,
            existing.context_id,
            existing.phase,
            existing.kind,
        ) != (start_id, start.context_id, phase, kind):
            raise DomainError(
                "factor_call_drift", "Upstream call identity changed", 409
            )
        return existing
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=start_id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_generation_closed", "Generation is already terminal", 409
        )
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, start_id)
    calls = session.scalars(
        select(FactorUpstreamCall)
        .filter_by(**_scope(fence), start_id=start_id, phase=phase)
        .order_by(FactorUpstreamCall.ordinal)
    ).all()
    if [call.ordinal for call in calls] != list(range(len(calls))) or len(
        calls
    ) >= 1000:
        raise DomainError(
            "factor_call_order_invalid", "Call ordinal closure differs", 409
        )
    row = FactorUpstreamCall(
        id=call_id,
        **_scope(fence),
        start_id=start_id,
        context_id=start.context_id,
        phase=phase,
        kind=kind,
        ordinal=len(calls),
    )
    session.add(row)
    session.flush()
    return row


def _closed_transport_chain(session, fence, operation):
    """Defend RD call closure against unresolved prior physical transport attempts."""
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
        ResearchModelOutcome,
        ResearchModelTransport,
    )
    from packages.research_runtime.budget_contracts import BudgetVector

    chain = session.scalars(
        select(ResearchModelTransport)
        .filter_by(operation_id=operation.id)
        .order_by(ResearchModelTransport.ordinal)
        .limit(257)
    ).all()
    if (
        not chain
        or len(chain) > 256
        or [row.ordinal for row in chain] != list(range(1, len(chain) + 1))
    ):
        raise DomainError(
            "factor_transport_chain_invalid",
            "Bounded contiguous physical attempts required",
            409,
        )
    completed = None
    for index, transport in enumerate(chain):
        state = "completed" if index == len(chain) - 1 else "failed"
        if (
            transport.operation_id != operation.id
            or transport.state != state
            or any(
                getattr(transport, key) != value for key, value in _scope(fence).items()
            )
        ):
            raise DomainError(
                "factor_transport_chain_incomplete",
                "Every physical attempt must be terminal",
                409,
            )
        outcome = session.scalar(
            select(ResearchModelOutcome).filter_by(transport_id=transport.id)
        )
        reservation = session.get(ResearchBudgetReservation, transport.reservation_id)
        settlement = session.scalar(
            select(ResearchBudgetSettlement).filter_by(
                reservation_id=transport.reservation_id
            )
        )
        if (
            outcome is None
            or outcome.outcome != state
            or reservation is None
            or reservation.state != "settled"
            or reservation.purpose != "model_transport"
            or settlement is None
            or any(
                any(getattr(row, key) != value for key, value in _scope(fence).items())
                for row in (outcome, reservation, settlement)
            )
        ):
            raise DomainError(
                "factor_transport_chain_unsettled",
                "Physical attempt outcome and budget must close",
                409,
            )
        held = BudgetVector.model_validate(reservation.amounts)
        charged = BudgetVector.model_validate(settlement.charge)
        usage = BudgetVector.model_validate(outcome.usage)
        if (
            held.calls != 1
            or usage.calls != 1
            or usage != charged
            or settlement.outcome
            != (
                "conservative_unknown"
                if operation.protocol
                in {
                    "mineagent.rdagent-provider-chat.v1",
                    "mineagent.rdagent-provider-embedding.v1",
                }
                else "measured"
            )
            or (
                operation.protocol
                in {
                    "mineagent.rdagent-provider-chat.v1",
                    "mineagent.rdagent-provider-embedding.v1",
                }
                and charged != held
            )
        ):
            raise DomainError(
                "factor_transport_accounting_drift",
                "Exact measured physical call accounting required",
                409,
            )
        if not held.covers(charged):
            raise DomainError(
                "factor_transport_charge_invalid",
                "Transport charge exceeds exact reservation",
                409,
            )
        completed = outcome
    return completed


def _completed_member_response(session, fence, member, settings):
    """Resolve outcome through transport to this exact immutable request operation."""
    from backend.app.research_broker_models import ResearchModelOperation
    from backend.app.research_object_services import read_research_object
    from packages.research_runtime.engine_broker_contracts import (
        RDChatResponse,
        RDEmbeddingResponse,
        parse_request,
        request_sha256,
        validate_response,
    )

    operation = _scope_row(session, ResearchModelOperation, member.operation_id, fence)
    from packages.rdagent_provider.codec import PROTOCOLS

    provider = member.protocol in PROTOCOLS
    if provider:
        from packages.rdagent_provider.codec import (
            parse_request,
            request_sha256,
            validate_response,
        )
        from packages.rdagent_provider.contracts import (
            RDProviderChatResponse,
            RDProviderEmbeddingResponse,
        )

        RDChatResponse, RDEmbeddingResponse = (
            RDProviderChatResponse,
            RDProviderEmbeddingResponse,
        )
    request = parse_request(member.protocol, operation.request)
    if (
        operation.context_id != member.context_id
        or operation.protocol != member.protocol
        or operation.request_sha256 != request_sha256(member.protocol, request)
        or operation.state != "completed"
    ):
        raise DomainError(
            "factor_model_operation_incomplete",
            "Exact completed request is required",
            409,
        )
    outcome = _closed_transport_chain(session, fence, operation)
    if (
        any(getattr(outcome, key) != value for key, value in _scope(fence).items())
        or outcome.response_object_kind != "model_response"
    ):
        raise DomainError("factor_response_scope", "Response scope differs", 409)
    raw = read_research_object(
        session,
        fence,
        outcome.response_object_id,
        "model_response",
        outcome.response_sha256,
        settings,
    )
    response_type = RDChatResponse if request.kind == "chat" else RDEmbeddingResponse
    response = response_type.model_validate_json(raw)
    response = validate_response(member.protocol, request, response)
    if provider:
        from backend.app.rdagent_provider_receipts import read_chain
        from packages.rdagent_provider.accounting import expected_usage

        _, config = require_research_spec(session, fence)
        read_chain(session, fence, outcome.transport_id, settings, completed=True)
        response, held = expected_usage(
            member.protocol, request, response, config.model_binding
        )
        if outcome.usage != held.model_dump():
            raise ValueError("Provider full conservative accounting differs")
    return outcome, response


def _call_members(session, fence, call_id):
    from backend.app.rdagent_models import FactorGenerationModelOperation

    members = session.scalars(
        select(FactorGenerationModelOperation)
        .filter_by(**_scope(fence), call_id=call_id)
        .order_by(FactorGenerationModelOperation.request_ordinal)
    ).all()
    if [member.request_ordinal for member in members] != list(range(len(members))):
        raise DomainError(
            "factor_request_order_invalid", "Request member ordinals do not close", 409
        )
    return members


def _verify_response_parts(session, fence, call, parts, settings):
    """Selected extraction/parse parts are exactly the last continuation chain."""
    members = _call_members(session, fence, call.id)
    if not members:
        raise DomainError(
            "factor_response_parts_empty", "Response parts are unavailable", 409
        )
    start = max(
        index
        for index, member in enumerate(members)
        if member.relation != "continuation"
    )
    selected = members[start:]
    if tuple(part.member_id for part in parts) != tuple(
        member.id for member in selected
    ):
        raise DomainError(
            "factor_response_parts_drift", "Selected response chain differs", 409
        )
    responses = []
    for part, member in zip(parts, selected, strict=True):
        outcome, response = _completed_member_response(session, fence, member, settings)
        if (part.outcome_id, part.response_object_id, part.response_sha256) != (
            outcome.id,
            outcome.response_object_id,
            outcome.response_sha256,
        ) or response.kind != "chat":
            raise DomainError(
                "factor_response_part_scope",
                "Response object is not this member's completed output",
                409,
            )
        responses.append(response)
    if [response.finish_reason for response in responses] != ["length"] * (
        len(responses) - 1
    ) + ["stop"]:
        raise DomainError(
            "factor_response_chain_unfinished",
            "Response continuation chain is unfinished",
            409,
        )
    return selected, responses


def _require_orchestrator_receipt(session, fence, admission_id, config):
    from backend.app.rdagent_controller_services import (
        controller_profile,
        require_recorded_controller,
    )
    from backend.app.research_process_models import ResearchProcessAdmission

    require_recorded_controller(session, fence, admission_id)
    admission = _scope_row(session, ResearchProcessAdmission, admission_id, fence)
    if (admission.role, admission.profile, admission.image_id) != (
        "rd_orchestrator",
        controller_profile(admission),
        config.runtime.orchestrator_image,
    ):
        raise DomainError(
            "factor_orchestrator_receipt_scope",
            "Receipt producer is not the fixed RD orchestrator",
            409,
        )
    return admission


def attach_request_operation(
    session,
    fence: AttemptFence,
    *,
    call_id: UUID,
    operation_id: UUID,
    idempotency_key: UUID,
    relation: str,
    settings,
    parse_failure_object_id: UUID | None = None,
    parse_failure_sha256: str | None = None,
):
    """Attach an already-admitted request, never submit transport or forge usage."""
    from backend.app.rdagent_models import (
        FactorGenerationModelOperation,
        FactorGenerationOutcome,
        FactorResponseAssembly,
        FactorSourcePublication,
        FactorUpstreamCall,
        FactorUpstreamCallOutcome,
    )
    from backend.app.research_broker_models import ResearchModelOperation
    from backend.app.research_object_services import read_research_object
    from packages.rdagent_adapter.host_evidence import ParseFailureReceipt
    from packages.research_runtime.engine_broker_contracts import (
        parse_request,
        request_sha256,
    )

    _, config = require_research_spec(session, fence)
    call = _scope_row(session, FactorUpstreamCall, call_id, fence, lock=True)
    protocol = (
        "mineagent.rdagent-chat.v1"
        if call.kind == "chat"
        else "mineagent.rdagent-embedding.v1"
    )
    if config.schema_version == "mineagent.factor-research.v2":
        from packages.rdagent_provider.codec import parse_request, request_sha256

        protocol = (
            "mineagent.rdagent-provider-chat.v1"
            if call.kind == "chat"
            else "mineagent.rdagent-provider-embedding.v1"
        )
    operation = _scope_row(session, ResearchModelOperation, operation_id, fence)
    request = parse_request(protocol, operation.request)
    if config.schema_version == "mineagent.factor-research.v2":
        from packages.rdagent_provider.contracts import require_request_binding

        require_request_binding(request, config.model_binding)
    expected_purpose = (
        "embedding"
        if call.kind == "embedding"
        else ("continuation" if relation == "continuation" else "chat")
    )
    if (
        operation.context_id != call.context_id
        or operation.protocol != protocol
        or operation.purpose != expected_purpose
        or operation.request_sha256 != request_sha256(protocol, request)
    ):
        raise DomainError(
            "factor_request_scope", "Request codec/context/purpose differs", 409
        )
    old = session.scalar(
        select(FactorGenerationModelOperation).filter_by(operation_id=operation_id)
    )
    if old is not None:
        if any(getattr(old, key) != value for key, value in _scope(fence).items()) or (
            old.call_id,
            old.idempotency_key,
            old.relation,
            old.protocol,
            old.parse_failure_object_id,
            old.parse_failure_sha256,
        ) != (
            call_id,
            idempotency_key,
            relation,
            protocol,
            parse_failure_object_id,
            parse_failure_sha256,
        ):
            raise DomainError(
                "factor_request_membership_drift", "Request membership changed", 409
            )
        return old
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=call.start_id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_generation_closed", "Generation is already terminal", 409
        )
    if (
        session.scalar(
            select(FactorResponseAssembly).filter_by(**_scope(fence), call_id=call_id)
        )
        is not None
    ):
        raise DomainError(
            "factor_source_call_closed",
            "Published response assembly closes this call to new requests",
            409,
        )
    if (
        session.scalar(
            select(FactorSourcePublication).filter_by(**_scope(fence), call_id=call_id)
        )
        is not None
    ):
        raise DomainError(
            "factor_source_call_closed",
            "Durable source pre-admission closes this call to new requests",
            409,
        )
    if (
        session.scalar(
            select(FactorUpstreamCallOutcome).filter_by(
                **_scope(fence), call_id=call_id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_call_closed", "Completed exchange rejects new request members", 409
        )
    members = _call_members(session, fence, call_id)
    if len(members) >= 60 or relation not in {
        "initial",
        "continuation",
        "semantic_repair",
    }:
        raise DomainError(
            "factor_request_relation_invalid", "Request relation or bound differs", 409
        )
    if not members:
        if relation != "initial":
            raise DomainError(
                "factor_initial_request_missing", "First request must be initial", 409
            )
    else:
        _, previous = _completed_member_response(session, fence, members[-1], settings)
        if (
            relation == "initial"
            or call.kind != "chat"
            or (relation == "continuation" and previous.finish_reason != "length")
        ):
            raise DomainError(
                "factor_continuation_not_authorized",
                "Observed response does not authorize relation",
                409,
            )
    if relation == "semantic_repair":
        if parse_failure_object_id is None or parse_failure_sha256 is None:
            raise DomainError(
                "factor_parse_receipt_required",
                "Semantic repair requires exact parser failure evidence",
                409,
            )
        raw = read_research_object(
            session,
            fence,
            parse_failure_object_id,
            "factor_parse_failure",
            parse_failure_sha256,
            settings,
        )
        receipt = ParseFailureReceipt.model_validate_json(raw)
        if receipt.call_id != call_id:
            raise DomainError(
                "factor_parse_receipt_drift",
                "Parser failure belongs to a different call",
                409,
            )
        _require_orchestrator_receipt(
            session, fence, receipt.orchestrator_admission_id, config
        )
        _verify_response_parts(session, fence, call, receipt.parts, settings)
    elif parse_failure_object_id is not None or parse_failure_sha256 is not None:
        raise DomainError(
            "factor_unexpected_parse_receipt",
            "Only semantic repair accepts parser failure evidence",
            409,
        )
    row = FactorGenerationModelOperation(
        id=uuid4(),
        **_scope(fence),
        context_id=call.context_id,
        start_id=call.start_id,
        call_id=call_id,
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        request_ordinal=len(members),
        relation=relation,
        predecessor_id=members[-1].id if members else None,
        protocol=protocol,
        parse_failure_object_id=parse_failure_object_id,
        parse_failure_object_kind="factor_parse_failure"
        if relation == "semantic_repair"
        else None,
        parse_failure_sha256=parse_failure_sha256,
    )
    session.add(row)
    session.flush()
    return row


def bind_generation_source(
    session,
    fence: AttemptFence,
    *,
    call_id: UUID,
    assembly_object_id: UUID,
    assembly_sha256: str,
    factor_version_id: UUID,
    settings,
):
    """Bind durable real source BEFORE any candidate admission/workspace injection.

    Source creation/storage uses existing factor publication separately; this
    transition verifies it and captures complete selected response provenance.
    Native upstream parser identity is an explicit trusted-orchestrator receipt,
    not a claim that the API has imported/re-executed upstream native code.
    """
    from backend.app.factor_models import FactorVersion
    from backend.app.factor_services import verify_version
    from backend.app.rdagent_models import (
        FactorGenerationOutcome,
        FactorGenerationSource,
        FactorGenerationStart,
        FactorResponseAssembly,
        FactorResponseAssemblyPart,
        FactorUpstreamCall,
        FactorUpstreamCallOutcome,
    )
    from backend.app.research_object_services import read_research_object
    from packages.rdagent_adapter.host_evidence import (
        ResponseAssemblyDocument,
        research_definition,
    )

    _, config = require_research_spec(session, fence)
    call = _scope_row(session, FactorUpstreamCall, call_id, fence, lock=True)
    if call.phase != "generation" or call.kind != "chat":
        raise DomainError(
            "factor_source_call_invalid",
            "Source must derive from a generation chat call",
            409,
        )
    closed = session.scalar(
        select(FactorUpstreamCallOutcome).filter_by(**_scope(fence), call_id=call.id)
    )
    if closed is None or closed.status != "completed":
        raise DomainError(
            "factor_source_exchange_incomplete",
            "Source requires authenticated completed exchange",
            409,
        )
    start = _scope_row(session, FactorGenerationStart, call.start_id, fence)
    raw = read_research_object(
        session,
        fence,
        assembly_object_id,
        "factor_response_assembly",
        assembly_sha256,
        settings,
    )
    document = ResponseAssemblyDocument.model_validate_json(raw)
    if document.call_id != call_id:
        raise DomainError(
            "factor_source_assembly_drift",
            "Response assembly belongs to another call",
            409,
        )
    _require_orchestrator_receipt(
        session, fence, document.orchestrator_admission_id, config
    )
    selected, responses = _verify_response_parts(
        session, fence, call, document.parts, settings
    )
    from backend.app.rdagent_source_witness_services import (
        persist_source_read_witness,
        require_exact_extracted_source,
    )

    require_exact_extracted_source(responses, document)
    version = session.scalar(
        select(FactorVersion).filter_by(owner_id=fence.owner_id, id=factor_version_id)
    )
    expected_definition = research_definition(config, document.source_sha256)
    expected_provenance = {
        "schema_version": "mineagent.rdagent-source.v1",
        "run_id": str(fence.run_id),
        "start_id": str(start.id),
        "call_id": str(call.id),
        "assembly_sha256": assembly_sha256,
        "explanation_origin": "task_declaration",
    }
    if version is None or (
        version.code,
        version.code_sha256,
        version.parent_version_id,
        version.source_kind,
        version.source_event_key,
        version.source_provenance,
        version.definition,
    ) != (
        document.source,
        document.source_sha256,
        start.parent_factor_version_id,
        "rdagent",
        f"rdagent:{start.id}",
        expected_provenance,
        expected_definition.model_dump(mode="json"),
    ):
        raise DomainError(
            "factor_source_version_drift",
            "Immutable factor source/definition/provenance differs",
            409,
        )
    verify_version(session, fence.owner_id, version, settings)
    existing = session.scalar(
        select(FactorGenerationSource).filter_by(**_scope(fence), start_id=start.id)
    )
    if existing is not None:
        assembly = _scope_row(
            session, FactorResponseAssembly, existing.assembly_id, fence
        )
        if (
            existing.call_id,
            existing.factor_version_id,
            existing.source_sha256,
            assembly.object_id,
            assembly.object_sha256,
        ) != (
            call_id,
            factor_version_id,
            document.source_sha256,
            assembly_object_id,
            assembly_sha256,
        ):
            raise DomainError(
                "factor_source_publication_drift",
                "Generation source publication changed",
                409,
            )
        persist_source_read_witness(
            session, fence, source_id=existing.id, settings=settings
        )
        return existing
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=start.id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_generation_closed", "Generation is already terminal", 409
        )
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, start.id)
    assembly = FactorResponseAssembly(
        id=uuid4(),
        **_scope(fence),
        start_id=start.id,
        context_id=start.context_id,
        call_id=call.id,
        object_id=assembly_object_id,
        object_kind="factor_response_assembly",
        object_sha256=assembly_sha256,
        part_count=len(selected),
    )
    session.add(assembly)
    session.flush()
    for ordinal, part in enumerate(document.parts):
        session.add(
            FactorResponseAssemblyPart(
                id=uuid4(),
                **_scope(fence),
                call_id=call.id,
                assembly_id=assembly.id,
                member_id=part.member_id,
                outcome_id=part.outcome_id,
                response_object_id=part.response_object_id,
                response_object_kind="model_response",
                response_sha256=part.response_sha256,
                ordinal=ordinal,
            )
        )
    source = FactorGenerationSource(
        id=uuid4(),
        **_scope(fence),
        context_id=start.context_id,
        start_id=start.id,
        factor_version_id=version.id,
        call_id=call.id,
        assembly_id=assembly.id,
        source_sha256=document.source_sha256,
    )
    session.add(source)
    session.flush()
    persist_source_read_witness(
        session, fence, source_id=source.id, settings=settings, allow_create=True
    )
    return source


def admit_atomic_request(
    session,
    fence: AttemptFence,
    *,
    call_id: UUID,
    idempotency_key: UUID,
    relation: str,
    request_bytes: bytes,
    request_object_id: UUID,
    settings,
    parse_failure_object_id: UUID | None = None,
    parse_failure_sha256: str | None = None,
):
    """Flush-only request+membership+first transport reservation admission.

    The caller's nonce is only a scoped replay key, never execution authority or
    a ModelOperation ID. IDs/ordinals come from the fenced host transaction.
    No external transport occurs here. Let failures escape/roll back this entire
    transaction; a separately published request object may remain conservatively
    charged for cleanup, never converted into a fabricated successful operation.
    """
    from backend.app.rdagent_models import (
        FactorGenerationModelOperation,
        FactorUpstreamCall,
    )
    from backend.app.research_broker_models import (
        ResearchContextAbort,
        ResearchModelOperation,
        ResearchModelTransport,
    )
    from backend.app.research_broker_services import reserve_budget
    from backend.app.research_object_services import read_research_object
    from packages.research_runtime.budget_contracts import BudgetVector
    from packages.research_runtime.engine_broker_contracts import (
        canonical_bytes,
        parse_request,
        request_sha256,
    )

    _, config = require_research_spec(session, fence)
    if type(idempotency_key) is not UUID or type(request_bytes) is not bytes:
        raise DomainError(
            "factor_atomic_request_invalid",
            "Exact nonce and canonical request bytes required",
            409,
        )
    call = _scope_row(session, FactorUpstreamCall, call_id, fence, lock=True)
    protocol = (
        "mineagent.rdagent-chat.v1"
        if call.kind == "chat"
        else "mineagent.rdagent-embedding.v1"
    )
    if config.schema_version == "mineagent.factor-research.v2":
        from packages.rdagent_provider.codec import (
            canonical_bytes,
            parse_request,
            request_sha256,
        )

        protocol = (
            "mineagent.rdagent-provider-chat.v1"
            if call.kind == "chat"
            else "mineagent.rdagent-provider-embedding.v1"
        )
    request = parse_request(protocol, request_bytes)
    if config.schema_version == "mineagent.factor-research.v2":
        from packages.rdagent_provider.contracts import require_request_binding

        require_request_binding(request, config.model_binding)
    if canonical_bytes(protocol, request) != request_bytes:
        raise DomainError(
            "factor_request_not_canonical",
            "Request bytes must match immutable canonical envelope",
            409,
        )
    request_hash = request_sha256(protocol, request)
    stored = read_research_object(
        session, fence, request_object_id, "model_request", request_hash, settings
    )
    if stored != request_bytes:
        raise DomainError(
            "factor_request_object_drift", "Published request bytes differ", 409
        )
    previous = session.scalar(
        select(FactorGenerationModelOperation).filter_by(
            **_scope(fence), call_id=call_id, idempotency_key=idempotency_key
        )
    )
    if previous is not None:
        operation = _scope_row(
            session, ResearchModelOperation, previous.operation_id, fence
        )
        if (
            operation.protocol,
            previous.relation,
            previous.parse_failure_object_id,
            previous.parse_failure_sha256,
            operation.request_object_id,
            operation.request_sha256,
            operation.request,
        ) != (
            protocol,
            relation,
            parse_failure_object_id,
            parse_failure_sha256,
            request_object_id,
            request_hash,
            request.model_dump(mode="json"),
        ):
            raise DomainError(
                "factor_atomic_replay_drift",
                "Request nonce was reused for different immutable input",
                409,
            )
        transport = session.scalar(
            select(ResearchModelTransport).filter_by(
                **_scope(fence), operation_id=operation.id, ordinal=1
            )
        )
        if transport is None:
            raise DomainError(
                "factor_atomic_admission_incomplete",
                "Atomic request lacks initial transport reservation",
                409,
            )
        return operation, previous, transport
    if config.schema_version == "mineagent.factor-research.v2":
        from backend.app.rdagent_provider_quota import require_provider_call_capacity

        require_provider_call_capacity(session, fence)
    if (
        session.scalar(
            select(ResearchContextAbort).filter_by(
                **_scope(fence), context_id=call.context_id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_context_closed", "Model context is already closed", 409
        )
    if (
        config.schema_version == "mineagent.factor-research.v2"
        and call.phase == "generation"
        and call.ordinal == 0
        and not _call_members(session, fence, call.id)
    ):
        from backend.app.rdagent_provider_prompt_guard import (
            require_first_provider_prompt,
        )

        if relation != "initial":
            raise ValueError("First provider generation request must be initial")
        require_first_provider_prompt(session, fence, call, request, config, settings)
    purpose = (
        "embedding"
        if call.kind == "embedding"
        else ("continuation" if relation == "continuation" else "chat")
    )
    operations = session.scalars(
        select(ResearchModelOperation)
        .filter_by(**_scope(fence), context_id=call.context_id, purpose=purpose)
        .order_by(ResearchModelOperation.ordinal)
    ).all()
    if [operation.ordinal for operation in operations] != list(
        range(len(operations))
    ) or len(operations) > 1000000:
        raise DomainError(
            "factor_operation_ordinals_invalid",
            "Host operation ordinal sequence differs",
            409,
        )
    operation = ResearchModelOperation(
        id=uuid4(),
        **_scope(fence),
        context_id=call.context_id,
        protocol=protocol,
        purpose=purpose,
        ordinal=len(operations),
        request=request.model_dump(mode="json"),
        request_object_id=request_object_id,
        request_object_kind="model_request",
        request_sha256=request_hash,
        state="open",
    )
    session.add(operation)
    session.flush()
    member = attach_request_operation(
        session,
        fence,
        call_id=call_id,
        operation_id=operation.id,
        idempotency_key=idempotency_key,
        relation=relation,
        settings=settings,
        parse_failure_object_id=parse_failure_object_id,
        parse_failure_sha256=parse_failure_sha256,
    )
    tokens = (
        (
            sum(len(message.content.encode("utf-8")) for message in request.messages)
            + request.max_output_tokens
        )
        if request.kind == "chat"
        else sum(len(text.encode("utf-8")) for text in request.inputs)
    )
    amounts = BudgetVector(calls=1, tokens=tokens)
    if config.schema_version == "mineagent.factor-research.v2":
        from packages.rdagent_provider.accounting import request_reservation

        amounts = request_reservation(request, config.model_binding)
        from backend.app.rdagent_provider_quota import admit_provider_capacity

        admit_provider_capacity(session, fence, amounts, settings)
    reservation = reserve_budget(
        session,
        fence,
        purpose="model_transport",
        logical_key=f"transport:{operation.id}:1",
        amounts=amounts,
    )
    transport = ResearchModelTransport(
        id=uuid4(),
        **_scope(fence),
        operation_id=operation.id,
        ordinal=1,
        reservation_id=reservation.id,
        state="reserved",
    )
    session.add(transport)
    session.flush()
    return operation, member, transport
