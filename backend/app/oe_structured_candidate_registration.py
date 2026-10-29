"""Actual observed JSON program→owner version→candidate→execution declaration.

Only an exact authenticated user seed or a physically stopped retained provider
response supplies program bytes. Caller source text must equal that source;
creating a candidate or input row does not launch an engine unit.
"""

from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.oe_structured_candidate_services import require_candidate_execution
from backend.app.oe_structured_controller_context import (
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_engine_services import prepare_execution
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredProviderExchange,
    OEStructuredSpec,
)
from backend.app.oe_structured_provider_evidence import read_completed_response
from backend.app.oe_structured_provider_operation import require_provider_operation
from backend.app.structured_factor_models import StructuredStrategyVersion
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import (
    CandidateDocument,
    project_candidate,
)
from packages.openevolve_structured.provider_response import parse_response
from runtimes.openevolve_structured_host.peer import ControllerBinding, WorkerBinding


def register_candidate(
    session, fence, epoch_id, upstream_program_id, source_bytes, settings, *, peer
):
    """Flush-only under caller lifecycle/native guard; exact replay creates no rows."""
    if type(upstream_program_id) is not UUID or type(source_bytes) is not bytes:
        fail()
    if type(peer.binding) is ControllerBinding:
        current = require_controller_context(
            session, fence, epoch_id, settings, peer=peer
        )
        expected = canonical_bytes(
            CandidateDocument(program=current.config.search.domain.seed_program)
        )
        if source_bytes != expected:
            fail()
        spec = session.get(OEStructuredSpec, fence.run_id)
        if spec is None or spec.owner_id != fence.owner_id:
            fail()
        version_id = spec.seed_program_version_id
        ordinal, parent_id, exchange_id = 0, None, None
    elif type(peer.binding) is WorkerBinding:
        bound = require_provider_operation(
            session, fence, peer.binding.intent_id, settings, peer=peer
        )
        if bound.intent.epoch_id != epoch_id:
            fail()
        exchange = session.scalar(
            select(OEStructuredProviderExchange).filter_by(
                iteration_intent_id=bound.intent.id
            )
        )
        scoped(exchange, fence)
        request, response = read_completed_response(
            session, fence, exchange, bound.config, settings
        )
        if request != bound.request:
            fail()
        generated = parse_response(
            request=request,
            response=response,
            binding=bound.config.model_binding,
            domain=bound.config.search.domain,
        )
        if source_bytes != generated.candidate_bytes:
            fail()
        current = require_controller_context(
            session, fence, epoch_id, settings, peer=peer, intent_id=bound.intent.id
        )
        ordinal, parent_id, exchange_id = (
            bound.intent.ordinal,
            bound.intent.parent_candidate_id,
            exchange.id,
        )
        version_id = None
    else:
        fail()
    projected = project_candidate(source_bytes, current.config.search.domain)
    row = session.scalar(
        select(OEStructuredCandidate).filter_by(run_id=fence.run_id, ordinal=ordinal)
    )
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    seed_origin = (
        "workflow_seed"
        if type(current.config) is WorkflowStructuredRunConfig
        else "authenticated_user_seed"
    )
    expected = {
        **{
            key: getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
        "epoch_id": epoch_id,
        "upstream_program_id": upstream_program_id,
        "ordinal": ordinal,
        "parent_candidate_id": parent_id,
        "provider_exchange_id": exchange_id,
        "candidate_canonical": source_bytes.decode("ascii"),
        "candidate_sha256": projected.candidate_sha256,
        "program_sha256": projected.program_sha256,
        "origin": seed_origin if ordinal == 0 else "model_proposal",
    }
    if row is not None:
        if any(getattr(row, key) != value for key, value in expected.items()) or (
            ordinal == 0 and row.program_version_id != version_id
        ):
            fail()
    else:
        if (
            session.scalar(
                select(OEStructuredCandidate).filter_by(
                    epoch_id=epoch_id, upstream_program_id=upstream_program_id
                )
            )
            is not None
        ):
            fail()
        if ordinal:
            parent = scoped(session.get(OEStructuredCandidate, parent_id), fence)
            if parent.epoch_id != epoch_id or not parent.ordinal < ordinal:
                fail()
            program = CandidateDocument.model_validate_json(source_bytes).program
            version = StructuredStrategyVersion(
                id=uuid4(),
                owner_id=fence.owner_id,
                parent_version_id=parent.program_version_id,
                program=program.model_dump(mode="json"),
                program_canonical=canonical_bytes(program).decode("ascii"),
                program_sha256=digest(program),
            )
            session.add(version)
            session.flush()
            version_id = version.id
        row = OEStructuredCandidate(
            id=uuid4(), program_version_id=version_id, **expected
        )
        session.add(row)
        session.flush()
    verified = require_candidate_execution(session, fence, row.id, settings, peer=peer)
    if (
        verified.candidate_bytes != source_bytes
        or verified.program_version_id != row.program_version_id
    ):
        fail()
    execution = prepare_execution(session, fence, row.id, settings, peer=peer)
    return row, execution
