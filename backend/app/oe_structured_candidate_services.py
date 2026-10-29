"""Fresh normal027 candidate authority, never a decoded-program launch grant."""

from dataclasses import asdict, dataclass
from uuid import UUID

from pydantic import TypeAdapter

from backend.app.factor_native_io import require_native_storage_guard
from backend.app.oe_structured_controller_context import (
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_identity import decode_candidate
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredProviderExchange,
)
from backend.app.oe_structured_operational_models import OEStructuredIterationIntent
from backend.app.oe_structured_provider_evidence import read_completed_response
from backend.app.research_lifecycle import require_neutral_transaction
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.openevolve_structured.provider_contracts import RenderedPrompt
from packages.openevolve_structured.provider_response import (
    build_request,
    parse_response,
)
from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig
from packages.research_runtime.process_contracts import canonical as process_canonical
from packages.research_runtime.process_contracts import digest as process_digest
from runtimes.openevolve_structured_host.peer import ControllerBinding, WorkerBinding
from runtimes.rdagent_operational.peer import KernelIdentity


@dataclass(frozen=True)
class CandidateExecutionBinding:
    candidate_id: UUID
    program_version_id: UUID
    provider_exchange_id: UUID | None
    parent_candidate_id: UUID | None
    config: ProviderStructuredRunConfig
    candidate_bytes: bytes
    candidate_sha256: str
    program_sha256: str


def require_candidate_execution(session, fence, candidate_id, settings, *, peer):
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_native_storage_guard(settings.storage_root)
    candidate = scoped(session.get(OEStructuredCandidate, candidate_id), fence)
    exchange = None
    intent = None
    if candidate.ordinal:
        exchange = scoped(
            session.get(OEStructuredProviderExchange, candidate.provider_exchange_id),
            fence,
        )
        intent = scoped(
            session.get(OEStructuredIterationIntent, exchange.iteration_intent_id),
            fence,
        )
        if (
            intent.epoch_id != candidate.epoch_id
            or exchange.epoch_id != candidate.epoch_id
            or intent.parent_candidate_id != candidate.parent_candidate_id
            or exchange.parent_candidate_id != candidate.parent_candidate_id
            or intent.ordinal != candidate.ordinal
        ):
            fail()
    context = require_controller_context(
        session,
        fence,
        candidate.epoch_id,
        settings,
        peer=peer,
        intent_id=None if intent is None else intent.id,
    )
    projected = decode_candidate(session, run, candidate)
    if intent is None:
        if (
            type(peer.binding) is not ControllerBinding
            or projected.candidate_sha256
            != context.bootstrap.spec.seed_candidate_sha256
        ):
            fail()
    else:
        if type(peer.binding) is not WorkerBinding:
            fail()
        from backend.app.oe_structured_population import require_selection

        require_selection(session, fence, intent, context.config, settings, peer=peer)
        worker = TypeAdapter(KernelIdentity).validate_json(
            intent.worker_identity_canonical
        )
        if (
            worker != peer.binding.worker
            or process_canonical(asdict(worker)).decode("ascii")
            != intent.worker_identity_canonical
            or process_digest(asdict(worker)) != intent.worker_identity_sha256
            or peer.binding.ticket_sha256 != intent.worker_ticket_sha256
        ):
            fail()
        operation = SearchOperationContext.model_validate_json(intent.context_canonical)
        rendered = RenderedPrompt.model_validate_json(intent.rendered_canonical)
        if (
            canonical_bytes(operation).decode("ascii") != intent.context_canonical
            or digest(operation) != intent.context_sha256
            or exchange.context_canonical != intent.context_canonical
            or exchange.context != operation.model_dump(mode="json")
            or exchange.context_sha256 != intent.context_sha256
            or canonical_bytes(rendered).decode("ascii") != intent.rendered_canonical
            or digest(rendered) != intent.rendered_sha256
            or operation.rendered_context_sha256 != intent.rendered_sha256
            or operation.selection_snapshot_sha256 != intent.selection_sha256
            or operation.iteration_intent_id != intent.id
            or operation.epoch_id != candidate.epoch_id
            or operation.native_iteration != intent.ordinal
            or operation.owner_id != fence.owner_id
            or operation.run_id != fence.run_id
            or operation.parent_candidate_id != candidate.parent_candidate_id
            or operation.execution_binding_sha256
            != context.config.search.execution_binding_sha256
        ):
            fail()
        parent = scoped(
            session.get(OEStructuredCandidate, candidate.parent_candidate_id), fence
        )
        decode_candidate(session, run, parent)
        if (
            parent.epoch_id != candidate.epoch_id
            or not parent.ordinal < candidate.ordinal
        ):
            fail()
        # Separate completed-parent reader validates retained physical/accounting
        # evidence, not the parent's historical live worker grant or a new launch.
        from backend.app.oe_structured_engine_results import read_parent_engine_outcome

        read_parent_engine_outcome(
            session, fence, parent.id, settings, peer=peer, current_intent_id=intent.id
        )
        request, response = read_completed_response(
            session, fence, exchange, context.config, settings
        )
        expected = build_request(
            binding=context.config.model_binding,
            context=operation,
            domain=context.config.search.domain,
            parent_candidate=parent.candidate_canonical.encode("ascii"),
            rendered=rendered,
        )
        if request != expected:
            fail()
        generated = parse_response(
            request=request,
            response=response,
            binding=context.config.model_binding,
            domain=context.config.search.domain,
        )
        if (
            generated.candidate_bytes != candidate.candidate_canonical.encode("ascii")
            or generated.candidate_sha256 != projected.candidate_sha256
            or generated.program_sha256 != projected.program_sha256
        ):
            fail()
    # Recheck current peer/fence after parent/source/SDK/result physical reads.
    if (
        require_controller_context(
            session,
            fence,
            candidate.epoch_id,
            settings,
            peer=peer,
            intent_id=None if intent is None else intent.id,
        )
        != context
    ):
        fail()
    return CandidateExecutionBinding(
        candidate.id,
        candidate.program_version_id,
        candidate.provider_exchange_id,
        candidate.parent_candidate_id,
        context.config,
        candidate.candidate_canonical.encode("ascii"),
        projected.candidate_sha256,
        projected.program_sha256,
    )


def require_parent_candidate(
    session, fence, parent_candidate_id, settings, *, peer, current_intent_id
):
    """Read-only parent provenance under the CURRENT worker, never a launch grant.

    At most two ancestors exist in this declared initial epoch. Their immutable
    generated bytes are reread, but no old worker is reconstructed or restarted.
    The separate parent engine reader owns actual stopped result qualification.
    """
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_native_storage_guard(settings.storage_root)
    current = scoped(session.get(OEStructuredIterationIntent, current_intent_id), fence)
    if (
        type(peer.binding) is not WorkerBinding
        or current.parent_candidate_id != parent_candidate_id
    ):
        fail()
    context = require_controller_context(
        session, fence, current.epoch_id, settings, peer=peer, intent_id=current.id
    )
    parent = scoped(session.get(OEStructuredCandidate, parent_candidate_id), fence)
    if (
        parent.epoch_id != current.epoch_id
        or not 0 <= parent.ordinal < current.ordinal <= 2
    ):
        fail()
    worker = TypeAdapter(KernelIdentity).validate_json(
        current.worker_identity_canonical
    )
    if (
        worker != peer.binding.worker
        or process_canonical(asdict(worker)).decode("ascii")
        != current.worker_identity_canonical
        or process_digest(asdict(worker)) != current.worker_identity_sha256
        or peer.binding.ticket_sha256 != current.worker_ticket_sha256
    ):
        fail()
    return _completed_candidate_provenance(
        session,
        fence,
        run,
        parent,
        context,
        settings,
        peer=peer,
        epoch_id=current.epoch_id,
        upper_ordinal=current.ordinal,
        current_intent_id=current.id,
    )


def _completed_candidate_provenance(
    session,
    fence,
    run,
    parent,
    context,
    settings,
    *,
    peer,
    epoch_id,
    upper_ordinal,
    current_intent_id,
):
    """Bounded immutable ancestry only; public readers establish exact purpose first."""
    binding = _retained_candidate_provenance(
        session,
        fence,
        run,
        parent,
        context.config,
        settings,
        epoch_id=epoch_id,
        upper_ordinal=upper_ordinal,
    )
    if (
        require_controller_context(
            session, fence, epoch_id, settings, peer=peer, intent_id=current_intent_id
        )
        != context
    ):
        fail()
    return binding


def _retained_candidate_provenance(
    session, fence, run, parent, config, settings, *, epoch_id, upper_ordinal
):
    """Immutable bytes only, NOT live authority or a launch permission.

    The fixed caller must independently establish its live or stopped purpose.
    """
    original = decode_candidate(session, run, parent)
    candidate = parent
    for _ in range(3):
        projected = decode_candidate(session, run, candidate)
        if candidate.ordinal == 0:
            if projected.candidate_sha256 != config.search.seed_candidate_sha256:
                fail()
            break
        exchange = scoped(
            session.get(OEStructuredProviderExchange, candidate.provider_exchange_id),
            fence,
        )
        intent = scoped(
            session.get(OEStructuredIterationIntent, exchange.iteration_intent_id),
            fence,
        )
        prior = scoped(
            session.get(OEStructuredCandidate, candidate.parent_candidate_id), fence
        )
        if (
            intent.epoch_id != epoch_id
            or exchange.epoch_id != epoch_id
            or prior.epoch_id != epoch_id
            or not 0 <= prior.ordinal < candidate.ordinal < upper_ordinal
            or intent.ordinal != candidate.ordinal
            or intent.parent_candidate_id != prior.id
            or exchange.parent_candidate_id != prior.id
        ):
            fail()
        operation = SearchOperationContext.model_validate_json(intent.context_canonical)
        rendered = RenderedPrompt.model_validate_json(intent.rendered_canonical)
        if (
            canonical_bytes(operation).decode("ascii") != intent.context_canonical
            or digest(operation) != intent.context_sha256
            or exchange.context != operation.model_dump(mode="json")
            or exchange.context_canonical != intent.context_canonical
            or exchange.context_sha256 != intent.context_sha256
            or canonical_bytes(rendered).decode("ascii") != intent.rendered_canonical
            or digest(rendered) != intent.rendered_sha256
            or operation.rendered_context_sha256 != intent.rendered_sha256
            or operation.selection_snapshot_sha256 != intent.selection_sha256
            or operation.iteration_intent_id != intent.id
            or operation.native_iteration != intent.ordinal
            or operation.epoch_id != epoch_id
            or operation.owner_id != fence.owner_id
            or operation.run_id != fence.run_id
            or operation.parent_candidate_id != prior.id
            or operation.execution_binding_sha256
            != config.search.execution_binding_sha256
        ):
            fail()
        request, response = read_completed_response(
            session, fence, exchange, config, settings
        )
        expected = build_request(
            binding=config.model_binding,
            context=operation,
            domain=config.search.domain,
            parent_candidate=prior.candidate_canonical.encode("ascii"),
            rendered=rendered,
        )
        if request != expected:
            fail()
        generated = parse_response(
            request=request,
            response=response,
            binding=config.model_binding,
            domain=config.search.domain,
        )
        if (
            generated.candidate_bytes != candidate.candidate_canonical.encode("ascii")
            or generated.candidate_sha256 != projected.candidate_sha256
            or generated.program_sha256 != projected.program_sha256
        ):
            fail()
        candidate = prior
    else:
        fail()
    return CandidateExecutionBinding(
        parent.id,
        parent.program_version_id,
        parent.provider_exchange_id,
        parent.parent_candidate_id,
        config,
        parent.candidate_canonical.encode("ascii"),
        original.candidate_sha256,
        original.program_sha256,
    )


def require_population_candidate(
    session, fence, candidate_id, settings, *, peer, current_intent_id
):
    """Read-only member of actual current BEFORE population; never launch authority.

    No metrics from this snapshot are trusted here. The outer population reader
    compares each member against freshly read actual engine measurements.
    """
    import hashlib

    from sqlalchemy import select

    from backend.app.oe_structured_operational_models import OEStructuredControlState
    from packages.openevolve_structured.checkpoint import (
        _json,
        _normalize,
        canonical,
        state_sha256,
    )

    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_native_storage_guard(settings.storage_root)
    current = scoped(session.get(OEStructuredIterationIntent, current_intent_id), fence)
    if type(peer.binding) is not WorkerBinding:
        fail()
    context = require_controller_context(
        session, fence, current.epoch_id, settings, peer=peer, intent_id=current.id
    )
    worker = TypeAdapter(KernelIdentity).validate_json(
        current.worker_identity_canonical
    )
    if (
        worker != peer.binding.worker
        or process_canonical(asdict(worker)).decode("ascii")
        != current.worker_identity_canonical
        or process_digest(asdict(worker)) != current.worker_identity_sha256
        or peer.binding.ticket_sha256 != current.worker_ticket_sha256
    ):
        fail()
    after = scoped(
        session.get(OEStructuredControlState, current.sampling_state_id), fence
    )
    before = scoped(session.get(OEStructuredControlState, after.previous_id), fence)
    latest = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=current.epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    if (
        latest is None
        or latest.id != after.id
        or before.epoch_id != current.epoch_id
        or after.epoch_id != current.epoch_id
        or before.phase not in {"seed", "applied"}
        or after.phase != "sampling"
        or after.sequence != before.sequence + 1
        or after.frontier != before.frontier
        or current.ordinal != before.frontier + 1
    ):
        fail()
    states = []
    for row in (before, after):
        raw = row.state_canonical.encode("utf-8")
        value = _json(raw)
        if type(value) is not dict or set(value) != {"metadata", "programs"}:
            fail()
        state = _normalize(
            value["metadata"], value["programs"], context.config.search.domain
        )
        if (
            canonical(state) != raw
            or state_sha256(state) != row.state_sha256
            or state["metadata"]["last_iteration"] != row.frontier
        ):
            fail()
        states.append(state)
    if canonical(states[0]["programs"]) != canonical(states[1]["programs"]):
        fail()
    candidate = scoped(session.get(OEStructuredCandidate, candidate_id), fence)
    member = states[0]["programs"].get(str(candidate.upstream_program_id))
    if (
        candidate.epoch_id != current.epoch_id
        or not 0 <= candidate.ordinal <= before.frontier
        or member is None
        or member["code"] != candidate.candidate_canonical
        or member["iteration_found"] != candidate.ordinal
        or hashlib.sha256(member["code"].encode("ascii")).hexdigest()
        != candidate.candidate_sha256
    ):
        fail()
    return _completed_candidate_provenance(
        session,
        fence,
        run,
        candidate,
        context,
        settings,
        peer=peer,
        epoch_id=current.epoch_id,
        upper_ordinal=current.ordinal,
        current_intent_id=current.id,
    )


def require_sampling_candidate(
    session, fence, candidate_id, settings, *, peer, current_control_id
):
    """Controller-only read of a member BEFORE a new physical worker submission.

    This does not create an intent, grant a worker ticket, or authorize execution.
    The exact latest seed/applied cursor derives the read boundary independently.
    """
    import hashlib

    from sqlalchemy import select

    from backend.app.oe_structured_operational_models import OEStructuredControlState
    from packages.openevolve_structured.checkpoint import (
        _json,
        _normalize,
        canonical,
        state_sha256,
    )

    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_native_storage_guard(settings.storage_root)
    if type(peer.binding) is not ControllerBinding:
        fail()
    control = scoped(session.get(OEStructuredControlState, current_control_id), fence)
    context = require_controller_context(
        session, fence, control.epoch_id, settings, peer=peer
    )
    latest = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=control.epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    if (
        latest is None
        or latest.id != control.id
        or control.phase not in {"seed", "applied"}
        or not 0 <= control.frontier < context.config.search.maximum_mutation_slots
        or control.sequence != 2 * control.frontier + 1
    ):
        fail()
    raw = control.state_canonical.encode("utf-8")
    value = _json(raw)
    if type(value) is not dict or set(value) != {"metadata", "programs"}:
        fail()
    state = _normalize(
        value["metadata"], value["programs"], context.config.search.domain
    )
    if (
        canonical(state) != raw
        or state_sha256(state) != control.state_sha256
        or state["metadata"]["last_iteration"] != control.frontier
    ):
        fail()
    candidate = scoped(session.get(OEStructuredCandidate, candidate_id), fence)
    member = state["programs"].get(str(candidate.upstream_program_id))
    if (
        candidate.epoch_id != control.epoch_id
        or not 0 <= candidate.ordinal <= control.frontier
        or member is None
        or member["code"] != candidate.candidate_canonical
        or member["iteration_found"] != candidate.ordinal
        or hashlib.sha256(member["code"].encode("ascii")).hexdigest()
        != candidate.candidate_sha256
    ):
        fail()
    result = _completed_candidate_provenance(
        session,
        fence,
        run,
        candidate,
        context,
        settings,
        peer=peer,
        epoch_id=control.epoch_id,
        upper_ordinal=control.frontier + 1,
        current_intent_id=None,
    )
    after = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=control.epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    if after is None or after.id != control.id:
        fail()
    return result


def require_terminal_candidate(
    session, fence, candidate_id, settings, *, peer, current_control_id
):
    """Controller-only exact terminal member read; never new execution authority.

    Before application: actual sampling BEFORE members or its unique generated
    child. Replay: exact latest terminal cursor and its immutable application.
    No outcome recursion; the fixed outer reader checks actual stopped bytes.
    """
    import hashlib

    from sqlalchemy import select

    from backend.app.oe_structured_operational_models import OEStructuredControlState
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
        OEStructuredWorkerOutcome,
    )
    from packages.openevolve_structured.checkpoint import (
        _json,
        _normalize,
        canonical,
        state_sha256,
    )

    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_native_storage_guard(settings.storage_root)
    if type(peer.binding) is not ControllerBinding:
        fail()
    candidate = scoped(session.get(OEStructuredCandidate, candidate_id), fence)
    context = require_controller_context(
        session, fence, candidate.epoch_id, settings, peer=peer
    )
    query = (
        select(OEStructuredControlState)
        .filter_by(epoch_id=candidate.epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    latest = session.scalar(query)
    if current_control_id is None:
        if latest is not None or candidate.ordinal != 0:
            fail()
        upper_ordinal = 1
    else:
        control = scoped(
            session.get(OEStructuredControlState, current_control_id), fence
        )
        if (
            latest is None
            or latest.id != control.id
            or control.epoch_id != candidate.epoch_id
        ):
            fail()
        raw = control.state_canonical.encode("utf-8")
        value = _json(raw)
        if type(value) is not dict or set(value) != {"metadata", "programs"}:
            fail()
        state = _normalize(
            value["metadata"], value["programs"], context.config.search.domain
        )
        if (
            canonical(state) != raw
            or state_sha256(state) != control.state_sha256
            or state["metadata"]["last_iteration"] != control.frontier
        ):
            fail()
        member = state["programs"].get(str(candidate.upstream_program_id))
        if control.phase == "sampling":
            ordinal = control.frontier + 1
            if (
                control.sequence != 2 * ordinal
                or not 1 <= ordinal <= context.config.search.maximum_mutation_slots
            ):
                fail()
            if candidate.ordinal == ordinal:
                exchange = scoped(
                    session.get(
                        OEStructuredProviderExchange, candidate.provider_exchange_id
                    ),
                    fence,
                )
                intent = scoped(
                    session.get(
                        OEStructuredIterationIntent, exchange.iteration_intent_id
                    ),
                    fence,
                )
                if (
                    member is not None
                    or intent.sampling_state_id != control.id
                    or intent.epoch_id != candidate.epoch_id
                    or intent.ordinal != ordinal
                    or intent.parent_candidate_id != candidate.parent_candidate_id
                ):
                    fail()
            elif not 0 <= candidate.ordinal <= control.frontier or member is None:
                fail()
            upper_ordinal = ordinal + 1
        elif control.phase in {"seed", "applied"}:
            application = scoped(
                session.scalar(
                    select(OEStructuredTerminalApplication).filter_by(
                        control_state_id=control.id
                    )
                ),
                fence,
            )
            if (
                application.epoch_id != candidate.epoch_id
                or application.ordinal != control.frontier
                or control.sequence != 2 * control.frontier + 1
                or not 0
                <= candidate.ordinal
                <= control.frontier
                <= context.config.search.maximum_mutation_slots
                or (control.phase == "seed") != (control.frontier == 0)
            ):
                fail()
            if member is None:
                worker = scoped(
                    session.get(
                        OEStructuredWorkerOutcome, application.worker_outcome_id
                    ),
                    fence,
                )
                intent = scoped(
                    session.get(
                        OEStructuredIterationIntent, worker.iteration_intent_id
                    ),
                    fence,
                )
                exchange = scoped(
                    session.get(
                        OEStructuredProviderExchange, candidate.provider_exchange_id
                    ),
                    fence,
                )
                if (
                    application.outcome != "rejected"
                    or worker.status != "evaluated"
                    or worker.epoch_id != control.epoch_id
                    or worker.candidate_id != candidate.id
                    or candidate.ordinal != control.frontier
                    or intent.epoch_id != control.epoch_id
                    or intent.ordinal != control.frontier
                    or intent.sampling_state_id != control.previous_id
                    or intent.parent_candidate_id != candidate.parent_candidate_id
                    or exchange.iteration_intent_id != intent.id
                ):
                    fail()
            upper_ordinal = control.frontier + 1
        else:
            fail()
        if member is not None and (
            member["code"] != candidate.candidate_canonical
            or member["iteration_found"] != candidate.ordinal
            or hashlib.sha256(member["code"].encode("ascii")).hexdigest()
            != candidate.candidate_sha256
        ):
            fail()
    result = _completed_candidate_provenance(
        session,
        fence,
        run,
        candidate,
        context,
        settings,
        peer=peer,
        epoch_id=candidate.epoch_id,
        upper_ordinal=upper_ordinal,
        current_intent_id=None,
    )
    after = session.scalar(query)
    if (after is None) != (latest is None) or (
        after is not None and after.id != latest.id
    ):
        fail()
    return result
