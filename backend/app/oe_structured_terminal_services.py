"""027 actual worker/terminal publications; flush-only, ACK only after commit.

Native state is an observed population, not a substitute for physical engine
results. Error/rejected application never creates a score or successful child.
"""

import hashlib
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from backend.app.oe_structured_controller_context import (
    SCOPE,
    fail,
    require_controller_context,
    scoped,
)
from backend.app.oe_structured_engine_results import (
    read_candidate_engine_outcome,
    read_terminal_engine_outcome,
)
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredEngineOutcome,
    OEStructuredExecution,
)
from backend.app.oe_structured_operational_models import (
    OEStructuredControlState,
    OEStructuredIterationIntent,
)
from backend.app.oe_structured_population import _state, measurement_feedback
from packages.openevolve_structured.checkpoint import (
    _json,
    _normalize,
    canonical,
    state_sha256,
)
from packages.openevolve_structured.worker_outcome import validate as validate_worker
from runtimes.openevolve_structured_host.peer import ControllerBinding


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _event(raw, fields):
    if type(raw) is not bytes or not 0 < len(raw) <= 1048576:
        fail()
    value = _json(raw)
    if type(value) is not dict or set(value) != fields or canonical(value) != raw:
        fail()
    return value


def _cursor(session, epoch_id):
    return session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )


def _transition(before, after, iteration, outcome, child_id):
    """No failed/rejected population mutation or unrelated accepted insertion."""
    if after["metadata"]["last_iteration"] != iteration:
        fail()
    if iteration == 0:
        if (
            outcome != "seed"
            or before is not None
            or set(after["programs"]) != {child_id}
        ):
            fail()
        return
    if before is None or before["metadata"]["last_iteration"] != iteration - 1:
        fail()
    if outcome in {"error", "rejected"}:
        expected = deepcopy(before)
        expected["metadata"]["last_iteration"] = iteration
        if canonical(after) != canonical(expected):
            fail()
    elif outcome == "accepted":
        # Capacity eight and at most three candidates: eviction is not part of
        # this profile, and old native program fields must remain byte-exact.
        if child_id is None or set(after["programs"]) != set(before["programs"]) | {
            child_id
        }:
            fail()
        for pid, program in before["programs"].items():
            if canonical(after["programs"][pid]) != canonical(program):
                fail()
    else:
        fail()


def _worker_document(row, domain):
    raw = row.evidence_canonical.encode("utf-8")
    value = validate_worker(_json(raw), domain)
    if canonical(value) != raw or _digest(raw) != row.evidence_sha256:
        fail()
    projection = value["projection"]
    if (
        projection["epoch_id"],
        projection["intent_id"],
        projection["status"],
        value["event_sha256"],
    ) != (
        str(row.epoch_id),
        str(row.iteration_intent_id),
        row.status,
        row.event_sha256,
    ):
        fail()
    ids = (row.candidate_id, row.execution_id, row.engine_outcome_id)
    if row.status == "worker_error":
        if (
            any(x is not None for x in ids)
            or projection["objective_ack_id"] is not None
        ):
            fail()
    elif any(x is None for x in ids) or projection["objective_ack_id"] != str(
        row.engine_outcome_id
    ):
        fail()
    return value


def register_worker_outcome(session, fence, raw, settings, *, peer):
    """Retain the actual worker event, never infer a child from an error."""
    from backend.app.oe_structured_terminal_models import OEStructuredWorkerOutcome
    from backend.app.oe_structured_worker_descriptor import require_worker_descriptor

    payload = _event(
        raw, {"grant", "iteration", "outcome", "child", "parent_id", "target_island"}
    )
    sampling = require_worker_descriptor(
        session, fence, payload["grant"], settings, peer=peer
    )
    intent = scoped(session.get(OEStructuredIterationIntent, sampling.id), fence)
    context = require_controller_context(
        session, fence, intent.epoch_id, settings, peer=peer, intent_id=intent.id
    )
    if type(payload["iteration"]) is not int or payload["iteration"] != intent.ordinal:
        fail()
    candidate = execution = engine = None
    if payload["outcome"] == "evaluated":
        from backend.app.oe_structured_provider_operation import (
            require_provider_operation,
        )

        require_provider_operation(session, fence, intent.id, settings, peer=peer)
        child = payload["child"]
        if type(child) is not dict:
            fail()
        candidate = scoped(
            session.scalar(
                select(OEStructuredCandidate).filter_by(
                    epoch_id=intent.epoch_id, ordinal=intent.ordinal
                )
            ),
            fence,
        )
        execution = scoped(
            session.scalar(
                select(OEStructuredExecution).filter_by(candidate_id=candidate.id)
            ),
            fence,
        )
        actual = read_candidate_engine_outcome(
            session, fence, execution.id, settings, peer=peer
        )
        engine = scoped(
            session.scalar(
                select(OEStructuredEngineOutcome).filter_by(execution_id=execution.id)
            ),
            fence,
        )
        parent = scoped(
            session.get(OEStructuredCandidate, intent.parent_candidate_id), fence
        )
        if (
            candidate.parent_candidate_id != parent.id
            or child.get("id") != str(candidate.upstream_program_id)
            or child.get("code") != candidate.candidate_canonical
            or payload["parent_id"] != str(parent.upstream_program_id)
            or not actual.measurement.eligible
            or canonical(child.get("metrics"))
            != canonical({"combined_score": actual.measurement.combined_score})
        ):
            fail()
    elif payload["outcome"] != "worker_error":
        fail()
    doc = {
        "schema_version": "mineagent.oe-structured-worker-outcome.v1",
        "projection": {
            "epoch_id": str(intent.epoch_id),
            "intent_id": str(intent.id),
            "native_iteration": intent.ordinal,
            "status": payload["outcome"],
            "objective_ack_id": None if engine is None else str(engine.id),
        },
        "observation": {
            k: payload[k]
            for k in ("iteration", "outcome", "child", "parent_id", "target_island")
        },
        "event_sha256": _digest(raw),
    }
    validate_worker(doc, context.config.search.domain)
    encoded = canonical(doc)
    values = {
        "epoch_id": intent.epoch_id,
        "iteration_intent_id": intent.id,
        "status": payload["outcome"],
        "candidate_id": None if candidate is None else candidate.id,
        "execution_id": None if execution is None else execution.id,
        "engine_outcome_id": None if engine is None else engine.id,
        "evidence_canonical": encoded.decode("utf-8"),
        "evidence_sha256": _digest(encoded),
        "event_sha256": _digest(raw),
    }
    previous = session.scalar(
        select(OEStructuredWorkerOutcome).filter_by(iteration_intent_id=intent.id)
    )
    if previous is not None:
        scoped(previous, fence)
        if any(getattr(previous, k) != v for k, v in values.items()):
            fail()
        return previous
    if (
        require_worker_descriptor(
            session, fence, payload["grant"], settings, peer=peer
        ).id
        != intent.id
    ):
        fail()
    row = OEStructuredWorkerOutcome(
        id=uuid4(), **{k: getattr(fence, k) for k in SCOPE}, **values
    )
    session.add(row)
    session.flush()
    return row


def register_terminal_application(session, fence, epoch_id, raw, settings, *, peer):
    """Publish contiguous actual native application after fresh physical checks.

    Caller owns native storage/lifecycle guard and transaction. Returning this
    row is not an ACK: socket handler must commit before acknowledging frontier.
    """
    from backend.app.oe_structured_sampling_models import OEStructuredSamplingIntent
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
        OEStructuredWorkerOutcome,
    )
    from backend.app.research_broker_models import ResearchBudgetReservation
    from backend.app.research_broker_services import settle_budget
    from packages.research_runtime.budget_contracts import BudgetVector

    event = _event(raw, {"iteration", "outcome", "native_state", "state_sha256"})
    iteration, outcome = event["iteration"], event["outcome"]
    context = require_controller_context(session, fence, epoch_id, settings, peer=peer)
    if (
        type(peer.binding) is not ControllerBinding
        or type(iteration) is not int
        or not 0 <= iteration <= context.config.search.maximum_mutation_slots
        or outcome
        not in ({"seed"} if iteration == 0 else {"accepted", "rejected", "error"})
    ):
        fail()
    supplied = event["native_state"]
    if type(supplied) is not dict or set(supplied) != {"metadata", "programs"}:
        fail()
    after = _normalize(
        supplied["metadata"], supplied["programs"], context.config.search.domain
    )
    if (
        canonical(after) != canonical(supplied)
        or state_sha256(after) != event["state_sha256"]
    ):
        fail()
    previous = session.scalar(
        select(OEStructuredTerminalApplication).filter_by(
            epoch_id=epoch_id, ordinal=iteration
        )
    )
    cursor = _cursor(session, epoch_id)
    if previous is not None:
        scoped(previous, fence)
        if (
            previous.event_canonical != raw.decode("utf-8")
            or previous.event_sha256 != _digest(raw)
            or cursor is None
            or cursor.id != previous.control_state_id
        ):
            fail()
        scoped(cursor, fence)
        if (
            cursor.phase != ("seed" if iteration == 0 else "applied")
            or _state(cursor, context.config.search.domain) != after
        ):
            fail()
        baseline = (
            None
            if iteration == 0
            else scoped(
                session.get(OEStructuredControlState, cursor.previous_id), fence
            )
        )
    else:
        baseline = cursor
    worker = intent = sampling = None
    before = None
    if iteration == 0:
        if baseline is not None:
            fail()
    else:
        scoped(baseline, fence)
        if (
            baseline.epoch_id != epoch_id
            or baseline.sequence != 2 * iteration
            or baseline.phase != "sampling"
            or baseline.frontier != iteration - 1
        ):
            fail()
        before = _state(baseline, context.config.search.domain)
        intent = scoped(
            session.scalar(
                select(OEStructuredIterationIntent).filter_by(
                    epoch_id=epoch_id, ordinal=iteration
                )
            ),
            fence,
        )
        if intent.sampling_state_id != baseline.id:
            fail()
        sampling = scoped(session.get(OEStructuredSamplingIntent, intent.id), fence)
        worker = scoped(
            session.scalar(
                select(OEStructuredWorkerOutcome).filter_by(
                    iteration_intent_id=intent.id
                )
            ),
            fence,
        )
        doc = _worker_document(worker, context.config.search.domain)
        if (
            doc["projection"]["native_iteration"] != iteration
            or (worker.status == "worker_error") != (outcome == "error")
            or (previous is not None and previous.worker_outcome_id != worker.id)
        ):
            fail()
    candidates = list(
        session.scalars(
            select(OEStructuredCandidate).filter_by(epoch_id=epoch_id).limit(4)
        )
    )
    if not candidates or len(candidates) > 3:
        fail()
    by_native, by_id = {}, {}
    for candidate in candidates:
        scoped(candidate, fence)
        if (
            str(candidate.upstream_program_id) in by_native
            or candidate.ordinal > iteration
        ):
            fail()
        by_native[str(candidate.upstream_program_id)] = candidate
        by_id[candidate.id] = candidate
    seed = next((c for c in candidates if c.ordinal == 0), None)
    child = seed if iteration == 0 else by_id.get(worker.candidate_id)
    if iteration and worker.status == "evaluated":
        if (
            child is None
            or child.ordinal != iteration
            or child.parent_candidate_id != intent.parent_candidate_id
        ):
            fail()
        parent = by_id.get(child.parent_candidate_id)
        returned = doc["observation"]["child"]
        if (
            parent is None
            or returned["id"] != str(child.upstream_program_id)
            or returned["code"] != child.candidate_canonical
            or returned["parent_id"] != str(parent.upstream_program_id)
            or doc["observation"]["parent_id"] != str(parent.upstream_program_id)
        ):
            fail()
    _transition(
        before,
        after,
        iteration,
        outcome,
        None if child is None else str(child.upstream_program_id),
    )
    # Evaluated-but-rejected children still require their actual result evidence.
    checked = set(after["programs"])
    if child is not None:
        checked.add(str(child.upstream_program_id))
    for pid in checked:
        candidate = by_native.get(pid)
        if candidate is None:
            fail()
        verified = read_terminal_engine_outcome(
            session,
            fence,
            candidate.id,
            settings,
            peer=peer,
            current_control_id=None if cursor is None else cursor.id,
        )
        measurement = verified.measurement
        if measurement.structured_strategy_version_id != candidate.program_version_id:
            fail()
        if (
            candidate is child
            and (iteration == 0 or outcome != "error")
            and not measurement.eligible
        ):
            fail()
        program = after["programs"].get(pid)
        parent = by_id.get(candidate.parent_candidate_id)
        if program is not None and (
            program["code"] != candidate.candidate_canonical
            or _digest(program["code"].encode()) != candidate.candidate_sha256
            or program["iteration_found"] != candidate.ordinal
            or program["parent_id"]
            != (None if parent is None else str(parent.upstream_program_id))
            or (candidate.ordinal > 0 and parent is None)
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
        if iteration and candidate is child:
            engine = scoped(
                session.get(OEStructuredEngineOutcome, worker.engine_outcome_id), fence
            )
            if (
                engine.candidate_id != candidate.id
                or engine.execution_id != verified.execution_id
                or worker.execution_id != verified.execution_id
                or doc["projection"]["objective_ack_id"] != str(engine.id)
                or doc["observation"]["child"]["metrics"]
                != {"combined_score": measurement.combined_score}
            ):
                fail()
            if program is not None:
                returned = doc["observation"]["child"]
                for key in (
                    "id",
                    "code",
                    "language",
                    "parent_id",
                    "generation",
                    "iteration_found",
                    "timestamp",
                    "changes_description",
                ):
                    if canonical(program[key]) != canonical(returned[key]):
                        fail()
    # The authenticated context and exact cursor must still be current after IO.
    latest = _cursor(session, epoch_id)
    if (None if latest is None else latest.id) != (
        None if cursor is None else cursor.id
    ) or require_controller_context(
        session, fence, epoch_id, settings, peer=peer
    ) != context:
        fail()
    if sampling is not None:
        reservation = scoped(
            session.get(ResearchBudgetReservation, sampling.mutation_reservation_id),
            fence,
        )
        if (
            reservation.purpose != "engine_mutation"
            or reservation.logical_key
            != f"oe-structured-mutation:{epoch_id}:{iteration}"
            or BudgetVector.model_validate(reservation.amounts)
            != BudgetVector(mutation_slots=1)
        ):
            fail()
        settle_budget(
            session,
            fence,
            reservation.id,
            charge=BudgetVector(mutation_slots=1),
            outcome="conservative_unknown",
        )
    if previous is not None:
        return previous
    scope = {k: getattr(fence, k) for k in SCOPE}
    control = OEStructuredControlState(
        id=uuid4(),
        **scope,
        epoch_id=epoch_id,
        previous_id=None if cursor is None else cursor.id,
        sequence=2 * iteration + 1,
        frontier=iteration,
        phase="seed" if iteration == 0 else "applied",
        state_canonical=canonical(after).decode("utf-8"),
        state_sha256=state_sha256(after),
    )
    terminal = OEStructuredTerminalApplication(
        id=uuid4(),
        **scope,
        epoch_id=epoch_id,
        ordinal=iteration,
        control_state_id=control.id,
        worker_outcome_id=None if worker is None else worker.id,
        outcome=outcome,
        event_canonical=raw.decode("utf-8"),
        event_sha256=_digest(raw),
    )
    session.add(control)
    session.add(terminal)
    session.flush()
    return terminal
