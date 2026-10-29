"""Actual worker rendering closes a prior sampling/placement, not vice versa."""

import hashlib
from dataclasses import asdict

from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_models import OEStructuredCandidate
from backend.app.oe_structured_operational_models import (
    OEStructuredControlState,
    OEStructuredIterationIntent,
)
from backend.app.oe_structured_provider_operation import require_provider_operation
from backend.app.oe_structured_sampling_models import (
    OEStructuredSamplingIntent,
    OEStructuredWorkerPlacement,
)
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.checkpoint import _json
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.openevolve_structured.provider_contracts import RenderedPrompt
from runtimes.openevolve_structured_host.peer import WorkerBinding


def record_rendering(session, fence, sampling_id, rendered_raw, settings, *, peer):
    """Flush-only; caller ACKs only after this entire evidence transaction commits."""
    if type(rendered_raw) is not bytes or not 0 < len(rendered_raw) <= 262144:
        fail()
    rendered = RenderedPrompt.model_validate_json(rendered_raw)
    if canonical_bytes(rendered) != rendered_raw:
        fail()
    sample = scoped(session.get(OEStructuredSamplingIntent, sampling_id), fence)
    context = require_controller_context(
        session, fence, sample.epoch_id, settings, peer=peer, intent_id=sample.id
    )
    if type(peer.binding) is not WorkerBinding:
        fail()
    placement = session.scalar(
        select(OEStructuredWorkerPlacement).filter_by(sampling_intent_id=sample.id)
    )
    scoped(placement, fence)
    cursor = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=sample.epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    scoped(cursor, fence)
    if (
        cursor.id != sample.sampling_state_id
        or cursor.phase != "sampling"
        or cursor.sequence != 2 * sample.ordinal
        or cursor.frontier != sample.ordinal - 1
        or peer.binding.ticket_sha256 != sample.worker_ticket_sha256
        or canonical_bytes(asdict(peer.binding.worker)).decode("ascii")
        != placement.worker_identity_canonical
        or digest(asdict(peer.binding.worker)) != placement.worker_identity_sha256
        or digest(asdict(peer.binding.controller))
        != placement.controller_identity_sha256
    ):
        fail()
    parent = scoped(
        session.get(OEStructuredCandidate, sample.parent_candidate_id), fence
    )
    if (
        parent.epoch_id != sample.epoch_id
        or rendered.current_candidate_sha256 != parent.candidate_sha256
    ):
        fail()
    selection = _json(sample.selection_canonical)
    programs = selection["snapshot"]["programs"]
    for reference in rendered.references:
        original = programs.get(str(reference.upstream_program_id))
        if (
            original is None
            or hashlib.sha256(original["code"].encode("ascii")).hexdigest()
            != reference.candidate_sha256
        ):
            fail()
    operation = SearchOperationContext(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        epoch_id=sample.epoch_id,
        iteration_intent_id=sample.id,
        native_iteration=sample.ordinal,
        parent_candidate_id=parent.id,
        parent_candidate_sha256=parent.candidate_sha256,
        execution_binding_sha256=context.config.search.execution_binding_sha256,
        selection_snapshot_sha256=sample.selection_sha256,
        rendered_context_sha256=digest(rendered),
    )
    expected = {
        **{
            key: getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        },
        "epoch_id": sample.epoch_id,
        "sampling_state_id": sample.sampling_state_id,
        "ordinal": sample.ordinal,
        "parent_candidate_id": parent.id,
        "selection_canonical": sample.selection_canonical,
        "selection_sha256": sample.selection_sha256,
        "context_canonical": canonical_bytes(operation).decode("ascii"),
        "context_sha256": digest(operation),
        "rendered_canonical": rendered_raw.decode("ascii"),
        "rendered_sha256": digest(rendered),
        "worker_ticket_sha256": sample.worker_ticket_sha256,
        "worker_identity_canonical": placement.worker_identity_canonical,
        "worker_identity_sha256": placement.worker_identity_sha256,
    }
    row = session.get(OEStructuredIterationIntent, sample.id)
    if row is None:
        row = OEStructuredIterationIntent(id=sample.id, **expected)
        session.add(row)
        session.flush()
    elif any(getattr(row, key) != value for key, value in expected.items()):
        fail()
    # Full selection + every actual engine output + provider ancestry are checked
    # before the first completed intent can commit. This is not a decoded grant.
    bound = require_provider_operation(session, fence, row.id, settings, peer=peer)
    if bound.context != operation:
        fail()
    return row, operation
