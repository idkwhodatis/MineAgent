"""027 persisted-candidate to fixed trusted-engine input boundary.

Fresh provider/controller eligibility belongs to the authoritative candidate
resolver. Decoding a candidate or accepting caller DTOs is never that authority.
No025 acceptance-run initializer or old domain resolver is used here.
"""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select, text

from backend.app.oe_structured_execution_input import (
    reconstruct_candidate_input,
    resolve_execution_input,
)
from backend.app.oe_structured_models import OEStructuredExecution
from backend.app.research_lifecycle import require_neutral_transaction
from packages.factors.contracts import digest
from packages.openevolve_structured.objective import StructuredObjective
from packages.research_runtime.process_contracts import ProcessError
from packages.structured_factor_strategy.wire import read_input

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def fail(code="oe_structured_engine_binding"):
    raise ProcessError(code)


def require_schema(session, fence):
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    ):
        fail("oe_structured_engine_schema")


def candidate_authority(session, fence, candidate_id, settings, *, peer):
    # Fixed resolver reauthenticates this actual nonserializable socket peer.
    from backend.app.oe_structured_candidate_services import require_candidate_execution

    return require_candidate_execution(
        session, fence, candidate_id, settings, peer=peer
    )


@dataclass(frozen=True)
class ResolvedOEExecution:
    execution_id: UUID
    candidate_id: UUID
    structured_strategy_version_id: UUID
    snapshot_id: UUID
    candidate_sha256: str
    program_sha256: str
    config_sha256: str
    strategy_input_sha256: str
    strategy_input_size_bytes: int
    market_sha256: str
    market_size_bytes: int
    engine_image_id: str
    engine_installed_parent_image_id: str
    engine_dependency_lock_sha256: str
    objective: StructuredObjective


def require_execution(session, fence, execution_id, settings, *, peer):
    require_schema(session, fence)
    row = session.get(OEStructuredExecution, execution_id)
    if row is None or any(getattr(row, key) != getattr(fence, key) for key in SCOPE):
        fail()
    authority = candidate_authority(
        session, fence, row.candidate_id, settings, peer=peer
    )
    return _resolved_execution(row, authority, fence)


def _resolved_execution(row, authority, fence):
    """Data projection after one of the explicit current or parent guards."""
    if row is None or any(getattr(row, key) != getattr(fence, key) for key in SCOPE):
        fail()
    config = authority.config
    if (
        authority.candidate_id,
        authority.program_version_id,
        config.search.owner_id,
        config.search.run_id,
        config.fixed_inputs.snapshot_id,
        config.fixed_inputs.market_sha256,
        config.fixed_inputs.market_size_bytes,
    ) != (
        row.candidate_id,
        row.program_version_id,
        fence.owner_id,
        fence.run_id,
        row.snapshot_id,
        row.market_sha256,
        row.market_size_bytes,
    ):
        fail()
    pins = config.runtime
    return ResolvedOEExecution(
        row.id,
        row.candidate_id,
        row.program_version_id,
        row.snapshot_id,
        authority.candidate_sha256,
        authority.program_sha256,
        digest(config),
        row.strategy_input_sha256,
        row.strategy_input_size_bytes,
        row.market_sha256,
        row.market_size_bytes,
        pins.engine_image_id,
        pins.engine_installed_parent_image_id,
        pins.engine_dependency_lock_sha256,
        config.search.objective,
    )


def execution_input(session, fence, execution_id, settings, *, peer):
    before = require_execution(session, fence, execution_id, settings, peer=peer)
    strategy, market = resolve_execution_input(session, fence, execution_id, settings)
    value = read_input(strategy)
    if (
        value.execution_id,
        value.structured_strategy_version_id,
        value.snapshot.snapshot_id,
        hashlib.sha256(strategy).hexdigest(),
        len(strategy),
        hashlib.sha256(market).hexdigest(),
        len(market),
    ) != (
        before.execution_id,
        before.structured_strategy_version_id,
        before.snapshot_id,
        before.strategy_input_sha256,
        before.strategy_input_size_bytes,
        before.market_sha256,
        before.market_size_bytes,
    ):
        fail()
    if require_execution(session, fence, execution_id, settings, peer=peer) != before:
        fail("oe_structured_engine_input_drift")
    return strategy, market


def prepare_execution(session, fence, candidate_id, settings, *, peer):
    """Flush one actual execution per persisted candidate; no process side effect."""
    require_schema(session, fence)
    authority = candidate_authority(session, fence, candidate_id, settings, peer=peer)
    if authority.candidate_id != candidate_id:
        fail()
    previous = session.scalar(
        select(OEStructuredExecution).where(
            OEStructuredExecution.candidate_id == candidate_id
        )
    )
    if previous is not None:
        execution_input(session, fence, previous.id, settings, peer=peer)
        return previous.id
    execution_id = uuid4()
    strategy, market = reconstruct_candidate_input(
        session,
        fence,
        candidate_id=candidate_id,
        execution_id=execution_id,
        settings=settings,
    )
    if (
        candidate_authority(session, fence, candidate_id, settings, peer=peer)
        != authority
    ):
        fail("oe_structured_engine_candidate_drift")
    value = read_input(strategy)
    if (
        value.execution_id,
        value.structured_strategy_version_id,
        value.snapshot.snapshot_id,
        hashlib.sha256(market).hexdigest(),
        len(market),
    ) != (
        execution_id,
        authority.program_version_id,
        authority.config.fixed_inputs.snapshot_id,
        authority.config.fixed_inputs.market_sha256,
        authority.config.fixed_inputs.market_size_bytes,
    ):
        fail()
    row = OEStructuredExecution(
        id=execution_id,
        **{key: getattr(fence, key) for key in SCOPE},
        candidate_id=candidate_id,
        program_version_id=authority.program_version_id,
        snapshot_id=value.snapshot.snapshot_id,
        strategy_input=value.model_dump(mode="json"),
        strategy_input_canonical=strategy.decode("ascii"),
        strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
        strategy_input_size_bytes=len(strategy),
        market_sha256=hashlib.sha256(market).hexdigest(),
        market_size_bytes=len(market),
    )
    session.add(row)
    session.flush()
    execution_input(session, fence, execution_id, settings, peer=peer)
    return execution_id


def require_parent_execution(
    session, fence, parent_candidate_id, settings, *, peer, current_intent_id
):
    """Current worker's exact previously completed parent; never launch authority."""
    from backend.app.oe_structured_candidate_services import require_parent_candidate

    require_schema(session, fence)
    authority = require_parent_candidate(
        session,
        fence,
        parent_candidate_id,
        settings,
        peer=peer,
        current_intent_id=current_intent_id,
    )
    if authority.candidate_id != parent_candidate_id:
        fail("oe_structured_parent_identity")
    row = session.scalar(
        select(OEStructuredExecution).filter_by(candidate_id=parent_candidate_id)
    )
    return _resolved_execution(row, authority, fence)


def require_population_execution(
    session, fence, candidate_id, settings, *, peer, current_intent_id
):
    """Current sampling input member only; this cannot admit a new engine unit."""
    from backend.app.oe_structured_candidate_services import (
        require_population_candidate,
    )

    require_schema(session, fence)
    authority = require_population_candidate(
        session,
        fence,
        candidate_id,
        settings,
        peer=peer,
        current_intent_id=current_intent_id,
    )
    if authority.candidate_id != candidate_id:
        fail("oe_structured_population_identity")
    row = session.scalar(
        select(OEStructuredExecution).filter_by(candidate_id=candidate_id)
    )
    return _resolved_execution(row, authority, fence)


def require_sampling_execution(
    session, fence, candidate_id, settings, *, peer, current_control_id
):
    """Pre-sampling controller member only; never an engine launch grant."""
    from backend.app.oe_structured_candidate_services import (
        require_sampling_candidate,
    )

    require_schema(session, fence)
    authority = require_sampling_candidate(
        session,
        fence,
        candidate_id,
        settings,
        peer=peer,
        current_control_id=current_control_id,
    )
    if authority.candidate_id != candidate_id:
        fail("oe_structured_sampling_identity")
    row = session.scalar(
        select(OEStructuredExecution).filter_by(candidate_id=candidate_id)
    )
    return _resolved_execution(row, authority, fence)


def require_terminal_execution(
    session, fence, candidate_id, settings, *, peer, current_control_id
):
    """Terminal controller member only; never an engine launch grant."""
    from backend.app.oe_structured_candidate_services import (
        require_terminal_candidate,
    )

    require_schema(session, fence)
    authority = require_terminal_candidate(
        session,
        fence,
        candidate_id,
        settings,
        peer=peer,
        current_control_id=current_control_id,
    )
    if authority.candidate_id != candidate_id:
        fail("oe_structured_terminal_identity")
    row = session.scalar(
        select(OEStructuredExecution).filter_by(candidate_id=candidate_id)
    )
    return _resolved_execution(row, authority, fence)
