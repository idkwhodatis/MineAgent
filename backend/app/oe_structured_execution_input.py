"""Fresh retained-byte reconstruction for027, not process admission/launch authority.

Reuses025's actual snapshot/market reader and unchanged trusted-engine assembler,
not its acceptance initializer or factor_strategy identity. The live027 issuer
must additionally resolve controller/provider-stop/candidate membership before
using these bytes. This module neither inserts rows nor issues process tickets.
"""

import hashlib
from dataclasses import dataclass

from backend.app.oe_structured_identity import decode_candidate, decode_spec, fail
from backend.app.oe_structured_models import OEStructuredCandidate
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.structured_factor_services import reconstruct_retained_input
from packages.factors.contracts import digest
from packages.structured_factor_strategy.wire import write_input


@dataclass(frozen=True)
class _ReconstructionDeclaration:
    """Exactly the pure025 assembler fields; deliberately NOT StructuredRunConfig."""

    structured_strategy_version_id: object
    program_sha256: str
    dataset_version_id: object
    dataset_sha256: str
    snapshots: tuple
    execution_window: object
    initial_cash: float
    source_policy: object
    engine_image_id: str
    engine_installed_parent_image_id: str
    engine_dependency_lock_sha256: str


def reconstruct_candidate_input(
    session, fence, *, candidate_id, execution_id, settings
):
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    # Existing026/025 fence dispatch intentionally cannot admit this new run.
    # Additive027 migration/fence dispatch is required; no fabricated old fence.
    _, config = decode_spec(session, run)
    candidate = session.get(OEStructuredCandidate, candidate_id)
    if candidate is None or any(
        getattr(candidate, key) != getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        fail()
    projected = decode_candidate(session, run, candidate)
    fixed, pins, objective = (
        config.fixed_inputs,
        config.runtime,
        config.search.objective,
    )
    declaration = _ReconstructionDeclaration(
        candidate.program_version_id,
        projected.program_sha256,
        fixed.dataset_version_id,
        fixed.dataset_sha256,
        (fixed.snapshot_id,),
        objective.window,
        objective.initial_cash,
        fixed.source_policy,
        pins.engine_image_id,
        pins.engine_installed_parent_image_id,
        pins.engine_dependency_lock_sha256,
    )
    value, market, dataset = reconstruct_retained_input(
        session,
        fence,
        execution_id,
        fixed.snapshot_id,
        declaration,
        projected.program,
        settings,
        declared_upload=fixed.schema_version
        == "mineagent.oe-structured-retained-inputs.v2",
    )
    if (
        value.snapshot.snapshot_id != fixed.snapshot_id
        or digest(value.snapshot) != fixed.snapshot_manifest_sha256
        or value.snapshot.input_sha256 != fixed.snapshot_input_sha256
        or value.snapshot.output_rows_sha256 != fixed.snapshot_output_sha256
        or value.snapshot.input.version.factor_version_id != fixed.factor_version_id
        or dataset.id != fixed.dataset_version_id
        or dataset.content_sha256 != fixed.dataset_sha256
        or hashlib.sha256(market).hexdigest() != fixed.market_sha256
        or len(market) != fixed.market_size_bytes
    ):
        fail()
    # Host parses only bounded canonical primitive rows/market JSON; no host
    # AKQuant execution, native file parser, labels or fabricated accounting.
    current_run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    _, current_config = decode_spec(session, current_run)
    if current_config != config:
        fail()
    return write_input(value), market


def resolve_execution_input(session, fence, execution_id, settings):
    """Reread exact persisted027 execution and its owned physical input bytes.

    This supplies input data only, not SDK/controller/candidate source authority,
    a process reservation, or permission to create/retry a native unit.
    """
    from backend.app.oe_structured_models import OEStructuredExecution
    from packages.structured_factor_strategy.wire import read_input

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    row = session.get(OEStructuredExecution, execution_id)
    if row is None or any(
        getattr(row, key) != getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    ):
        fail()
    strategy, market = reconstruct_candidate_input(
        session,
        fence,
        candidate_id=row.candidate_id,
        execution_id=row.id,
        settings=settings,
    )
    value = read_input(strategy)
    if (
        value.execution_id != row.id
        or value.structured_strategy_version_id != row.program_version_id
        or value.snapshot.snapshot_id != row.snapshot_id
        or value.market_sha256 != row.market_sha256
        or strategy.decode("ascii") != row.strategy_input_canonical
        or value.model_dump(mode="json") != row.strategy_input
        or hashlib.sha256(strategy).hexdigest() != row.strategy_input_sha256
        or len(strategy) != row.strategy_input_size_bytes
        or hashlib.sha256(market).hexdigest() != row.market_sha256
        or len(market) != row.market_size_bytes
    ):
        fail()
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    return strategy, market
