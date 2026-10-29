"""Persist actual pre-dispatch sampling without inventing a rendered worker row."""

import hashlib
import hmac
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_engine_results import read_sampling_engine_outcome
from backend.app.oe_structured_models import OEStructuredCandidate
from backend.app.oe_structured_operational_models import OEStructuredControlState
from backend.app.oe_structured_population import (
    _selection,
    _state,
    measurement_feedback,
)
from backend.app.oe_structured_sampling_models import OEStructuredSamplingIntent
from backend.app.research_broker_services import reserve_budget
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.checkpoint import _json, canonical, state_sha256
from packages.openevolve_structured.host_events import SamplingPayload
from packages.openevolve_structured.sampling_validation import (
    validate_sampling_transition,
)
from packages.research_runtime.budget_contracts import BudgetVector
from runtimes.openevolve_structured_host.peer import ControllerBinding


@dataclass(frozen=True)
class SelectionData:
    """Pure projection for closed selection validation, not a persisted intent."""

    ordinal: int
    selection_canonical: str
    selection_sha256: str


def worker_ticket(key, epoch_id, sampling_id):
    if type(key) is not bytes or len(key) != 32:
        fail()
    return hmac.new(
        key,
        f"oe-structured-worker-v1:{epoch_id}:{sampling_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def register_sampling(session, fence, epoch_id, raw, settings, *, peer, ticket_key):
    """Flush-only; commit the real sampling cursor and mutation hold before ACK."""
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        fail()
    payload = SamplingPayload.model_validate_json(raw)
    if canonical(payload.model_dump(mode="json")) != raw:
        fail()
    context = require_controller_context(session, fence, epoch_id, settings, peer=peer)
    if type(peer.binding) is not ControllerBinding:
        fail()
    selection = {
        "schema_version": "mineagent.oe-structured-selection.v1",
        "native_iteration": payload.iteration,
        "parent_id": str(payload.parent_id),
        "inspiration_ids": [str(x) for x in payload.inspiration_ids],
        "snapshot": payload.snapshot,
    }
    selection_raw = canonical_bytes(selection)
    projected = SelectionData(
        payload.iteration, selection_raw.decode("ascii"), digest(selection)
    )
    if (
        len(selection_raw) > 65536
        or payload.iteration > context.config.search.maximum_mutation_slots
    ):
        fail()
    # Retry does not repeat physical submission. Only the same immutable actual
    # event under this still-live controller can recover its lost ACK descriptor.
    previous = session.scalar(
        select(OEStructuredSamplingIntent).filter_by(
            epoch_id=epoch_id, ordinal=payload.iteration
        )
    )
    if previous is not None:
        scoped(previous, fence)
        if (
            previous.event_sha256 != hashlib.sha256(raw).hexdigest()
            or previous.selection_canonical != projected.selection_canonical
        ):
            fail()
        latest = session.scalar(
            select(OEStructuredControlState)
            .filter_by(epoch_id=epoch_id)
            .order_by(OEStructuredControlState.sequence.desc())
            .limit(1)
        )
        if latest is None or latest.id != previous.sampling_state_id:
            fail()
        token = worker_ticket(ticket_key, epoch_id, previous.id)
        if hashlib.sha256(token.encode()).hexdigest() != previous.worker_ticket_sha256:
            fail()
        return previous, token
    cursor = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    scoped(cursor, fence)
    if (
        cursor.phase not in {"seed", "applied"}
        or cursor.frontier != payload.iteration - 1
        or cursor.sequence != 2 * payload.iteration - 1
        or cursor.state_sha256 != payload.sampling.before_state_sha256
    ):
        fail()
    before = _state(cursor, context.config.search.domain)
    after = validate_sampling_transition(
        before,
        payload.sampling.after_state,
        str(payload.parent_id),
        payload.snapshot,
        context.config.search.domain,
    )
    rows = list(
        session.scalars(
            select(OEStructuredCandidate).filter_by(epoch_id=epoch_id).limit(4)
        )
    )
    if not rows or len(rows) > 3:
        fail()
    by_native, by_id = {}, {}
    for candidate in rows:
        scoped(candidate, fence)
        if (
            candidate.epoch_id != epoch_id
            or str(candidate.upstream_program_id) in by_native
        ):
            fail()
        by_native[str(candidate.upstream_program_id)] = candidate
        by_id[candidate.id] = candidate
    parent = by_native.get(str(payload.parent_id))
    if parent is None:
        fail()
    _selection(projected, before, parent)
    for pid, program in before["programs"].items():
        candidate = by_native.get(pid)
        if candidate is None or not 0 <= candidate.ordinal < payload.iteration:
            fail()
        ancestor = by_id.get(candidate.parent_candidate_id)
        if (
            program["code"] != candidate.candidate_canonical
            or program["iteration_found"] != candidate.ordinal
            or program["parent_id"]
            != (None if ancestor is None else str(ancestor.upstream_program_id))
            or (candidate.ordinal > 0 and ancestor is None)
        ):
            fail()
        result = read_sampling_engine_outcome(
            session,
            fence,
            candidate.id,
            settings,
            peer=peer,
            current_control_id=cursor.id,
        )
        measurement = result.measurement
        if (
            result.candidate_id != candidate.id
            or measurement.structured_strategy_version_id
            != candidate.program_version_id
            or canonical(program["metrics"])
            != canonical(
                {"combined_score": measurement.combined_score}
                if measurement.eligible
                else {}
            )
            or program["artifacts_json"] is None
            or _json(program["artifacts_json"])
            != {"structured_feedback": measurement_feedback(measurement)}
        ):
            fail()
    held = reserve_budget(
        session,
        fence,
        purpose="engine_mutation",
        logical_key=f"oe-structured-mutation:{epoch_id}:{payload.iteration}",
        amounts=BudgetVector(mutation_slots=1),
        settings=settings,
    )
    if held.state != "reserved":
        fail()
    current = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    if (
        current is None
        or current.id != cursor.id
        or require_controller_context(session, fence, epoch_id, settings, peer=peer)
        != context
    ):
        fail()
    scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    state = OEStructuredControlState(
        id=uuid4(),
        **scope,
        epoch_id=epoch_id,
        previous_id=cursor.id,
        sequence=cursor.sequence + 1,
        frontier=cursor.frontier,
        phase="sampling",
        state_canonical=canonical(after).decode("utf-8"),
        state_sha256=state_sha256(after),
    )
    sampling_id = uuid4()
    token = worker_ticket(ticket_key, epoch_id, sampling_id)
    row = OEStructuredSamplingIntent(
        id=sampling_id,
        **scope,
        epoch_id=epoch_id,
        sampling_state_id=state.id,
        parent_candidate_id=parent.id,
        mutation_reservation_id=held.id,
        ordinal=payload.iteration,
        selection_canonical=projected.selection_canonical,
        selection_sha256=projected.selection_sha256,
        event_sha256=hashlib.sha256(raw).hexdigest(),
        worker_ticket_sha256=hashlib.sha256(token.encode()).hexdigest(),
    )
    session.add(state)
    session.add(row)
    session.flush()
    return row, token
