"""Exact final checkpoint readiness, distinct from trusted controller stop.

A positive pre-stop answer permits only the final publication ACK. It is not a
run-success, release, stop, or budget-settlement receipt.
"""

from sqlalchemy import select

from backend.app.oe_structured_checkpoint_models import (
    OEStructuredCheckpointCompletion,
    OEStructuredCheckpointIntent,
    OEStructuredCheckpointPublication,
    OEStructuredCheckpointUpload,
)
from backend.app.oe_structured_checkpoint_services import (
    controller,
    current_state,
    native_files_state,
    retained_files,
)
from backend.app.oe_structured_checkpoint_upload import require_upload_hold
from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_operational_models import OEStructuredControlState
from backend.app.oe_structured_population import _state
from packages.openevolve_structured.checkpoint import state_sha256


def _rows(session, model, epoch_id):
    return list(session.scalars(select(model).filter_by(epoch_id=epoch_id).limit(6)))


def _published_checkpoints(session, fence, epoch_id, config, settings):
    """Bounded full inventory, with exact reverse membership and actual bytes."""
    intents = _rows(session, OEStructuredCheckpointIntent, epoch_id)
    uploads = _rows(session, OEStructuredCheckpointUpload, epoch_id)
    completions = _rows(session, OEStructuredCheckpointCompletion, epoch_id)
    publications = _rows(session, OEStructuredCheckpointPublication, epoch_id)
    groups = (intents, uploads, completions, publications)
    for rows in groups:
        if len(rows) > 4:
            fail()
        for row in rows:
            scoped(row, fence)
            if row.epoch_id != epoch_id:
                fail()
    if len({row.id for rows in groups for row in rows}) != sum(map(len, groups)):
        fail()
    intents.sort(key=lambda row: row.save_ordinal)
    if [row.save_ordinal for row in intents] != list(range(1, len(intents) + 1)):
        fail()
    if not all(len(rows) == len(intents) for rows in groups):
        fail()
    by_intent = {row.intent_id: row for row in uploads}
    by_upload = {row.upload_id: row for row in completions}
    published = {row.intent_id: row for row in publications}
    if (len(by_intent), len(by_upload), len(published)) != (len(intents),) * 3:
        fail()
    verified = []
    for intent in intents:
        upload = by_intent.get(intent.id)
        publication = published.get(intent.id)
        completion = None if upload is None else by_upload.get(upload.id)
        if upload is None or completion is None or publication is None:
            fail()
        if (
            publication.completion_id,
            publication.control_state_id,
            publication.state_sha256,
        ) != (completion.id, intent.control_state_id, intent.state_sha256):
            fail()
        cursor = scoped(
            session.get(OEStructuredControlState, intent.control_state_id), fence
        )
        if (
            cursor.epoch_id != epoch_id
            or cursor.phase not in {"seed", "applied"}
            or cursor.frontier != intent.frontier
        ):
            fail()
        state = native_files_state(
            retained_files(session, fence, intent, completion, settings),
            config.search.domain,
        )
        if (
            state != _state(cursor, config.search.domain)
            or state_sha256(state) != intent.state_sha256
        ):
            fail()
        verified.append((intent, upload, cursor))
    return verified


def is_final_checkpoint_ready(session, fence, epoch_id, settings, *, peer):
    """Fresh real controller + complete final files; never substitutes for EOF."""
    context = controller(session, fence, epoch_id, settings, peer)
    expected = context.config.search.maximum_mutation_slots + 2
    intents = _rows(session, OEStructuredCheckpointIntent, epoch_id)
    if not intents:
        return False
    if len(intents) < expected:
        return False
    if len(intents) != expected:
        fail()
    verified = _published_checkpoints(
        session, fence, epoch_id, context.config, settings
    )
    if len(verified) != expected:
        fail()
    for _, upload, _ in verified:
        require_upload_hold(session, fence, upload)
    current, _ = current_state(session, fence, epoch_id, context, settings, peer)
    last, _, cursor = verified[-1]
    if (last.save_ordinal, last.frontier, cursor.id) != (
        expected,
        context.config.search.maximum_mutation_slots,
        current.id,
    ):
        fail()
    if controller(session, fence, epoch_id, settings, peer) != context:
        fail()
    return True


def _stopped_controller(session, fence, epoch_id, settings):
    """Fresh fenced immutable declaration and original physical stop, no Peer."""
    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.oe_structured_controller_process_services import (
        _expected,
        _persisted,
    )
    from backend.app.oe_structured_epoch_services import expected_controller_bootstrap
    from backend.app.oe_structured_identity import decode_spec
    from backend.app.research_lifecycle import require_neutral_transaction
    from runtimes.openevolve_structured_host.completion_journal import (
        read_controller_completion,
    )
    from runtimes.openevolve_structured_host.process_journal import (
        read_controller_journal,
    )

    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    require_native_storage_guard(settings.storage_root)
    epoch, bootstrap, _ = expected_controller_bootstrap(
        session, fence, epoch_id, settings
    )
    _, config = decode_spec(session, run)
    admission, state, receipts, intent = _persisted(
        session, fence, epoch.controller_admission_id
    )
    _expected(session, fence, intent, settings)
    if state.status != "stopped" or set(receipts) != {
        "launch_intent",
        "launch_identity",
        "controller_child",
        "stop",
        "controller_completion",
    }:
        fail()
    launch, identity, child, stop = read_controller_journal(
        settings.storage_root, admission, with_child=True, with_stop=True
    )
    expected = {
        "launch_intent": intent,
        "launch_identity": identity,
        "controller_child": child,
        "stop": stop,
        "controller_completion": read_controller_completion(
            settings.storage_root, admission
        ),
    }
    if launch.intent != intent or any(
        value is None or receipts[k].payload != value.model_dump(mode="json")
        for k, value in expected.items()
    ):
        fail()
    _settled(session, fence, admission.reservation_id)
    return run, config, epoch, bootstrap, admission


def _settled(session, fence, reservation_id):
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
    )

    held = scoped(session.get(ResearchBudgetReservation, reservation_id), fence)
    paid = scoped(
        session.scalar(
            select(ResearchBudgetSettlement).filter_by(reservation_id=held.id)
        ),
        fence,
    )
    if (
        held.state != "settled"
        or paid.outcome != "conservative_unknown"
        or paid.charge != held.amounts
    ):
        fail()
    return held


def _stopped_engine(session, fence, candidate, config, run, epoch_id, settings):
    from backend.app.oe_structured_candidate_services import (
        _retained_candidate_provenance,
    )
    from backend.app.oe_structured_engine_results import _expected, _read_physical
    from backend.app.oe_structured_engine_services import _resolved_execution
    from backend.app.oe_structured_models import (
        OEStructuredEngineOutcome,
        OEStructuredExecution,
    )

    authority = _retained_candidate_provenance(
        session,
        fence,
        run,
        candidate,
        config,
        settings,
        epoch_id=epoch_id,
        upper_ordinal=config.search.maximum_mutation_slots + 1,
    )
    rows = list(
        session.scalars(
            select(OEStructuredExecution).filter_by(candidate_id=candidate.id).limit(2)
        )
    )
    if len(rows) != 1:
        fail()
    execution = scoped(rows[0], fence)
    outcome = scoped(
        session.scalar(
            select(OEStructuredEngineOutcome).filter_by(execution_id=execution.id)
        ),
        fence,
    )
    if outcome.candidate_id != candidate.id:
        fail()
    pins = _resolved_execution(execution, authority, fence)
    _, receipt, _, measurement = _read_physical(
        session, fence, pins, outcome.admission_id, settings
    )
    if any(
        getattr(outcome, key) != value
        for key, value in _expected(pins, receipt, measurement).items()
    ):
        fail()
    _settled(session, fence, receipt.admission.reservation_id)
    return outcome, measurement


def complete_stopped_epoch(session, fence, epoch_id, settings):
    """Flush-only post-stop qualification and upload settlement, never run status.

    Caller holds the actual native/lifecycle guard and commits atomically before
    treating the return as acknowledged. No dead socket peer is reconstructed.
    """
    from backend.app.oe_structured_models import OEStructuredCandidate
    from backend.app.oe_structured_population import measurement_feedback
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
        OEStructuredWorkerOutcome,
    )
    from backend.app.oe_structured_terminal_services import (
        _event,
        _transition,
        _worker_document,
    )
    from backend.app.research_broker_models import ResearchBudgetReservation
    from backend.app.research_broker_services import settle_budget
    from packages.openevolve_structured.checkpoint import _json, canonical
    from packages.research_runtime.budget_contracts import BudgetVector

    run, config, epoch, _bootstrap, admission = _stopped_controller(
        session, fence, epoch_id, settings
    )
    maximum = config.search.maximum_mutation_slots
    checkpoints = _published_checkpoints(session, fence, epoch_id, config, settings)
    if len(checkpoints) != maximum + 2:
        fail()
    controls = sorted(
        _rows(session, OEStructuredControlState, epoch_id), key=lambda row: row.sequence
    )
    if len(controls) != 2 * maximum + 1 or any(
        row.epoch_id != epoch_id for row in controls
    ):
        fail()
    previous = None
    states = []
    for number, row in enumerate(controls, 1):
        scoped(row, fence)
        if (
            row.sequence != number
            or row.previous_id != (None if previous is None else previous.id)
            or row.phase
            != ("seed" if number == 1 else "sampling" if number % 2 == 0 else "applied")
            or row.frontier != (number - 1) // 2
        ):
            fail()
        state = _state(row, config.search.domain)
        if row.phase == "sampling" and state["programs"] != states[-1]["programs"]:
            fail()
        states.append(state)
        previous = row
    if (
        checkpoints[-1][2].id != controls[-1].id
        or checkpoints[-1][0].frontier != maximum
    ):
        fail()
    candidates = _rows(session, OEStructuredCandidate, epoch_id)
    if not 1 <= len(candidates) <= maximum + 1 or len(
        {c.ordinal for c in candidates}
    ) != len(candidates):
        fail()
    by_id, by_native, outcomes = {}, {}, {}
    for candidate in candidates:
        scoped(candidate, fence)
        if not 0 <= candidate.ordinal <= maximum:
            fail()
        by_id[candidate.id] = candidate
        by_native[str(candidate.upstream_program_id)] = candidate
        outcomes[candidate.id] = _stopped_engine(
            session, fence, candidate, config, run, epoch_id, settings
        )
    # No orphan engine or SDK obligations may disappear behind a final cursor.
    from backend.app.oe_structured_models import OEStructuredProviderExchange
    from backend.app.oe_structured_operational_models import (
        OEStructuredProviderTransport,
    )
    from backend.app.research_process_models import ResearchProcessAdmission

    exchanges = _rows(session, OEStructuredProviderExchange, epoch_id)
    exact_scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    members = list(
        session.scalars(
            select(OEStructuredProviderTransport).filter_by(**exact_scope).limit(4)
        )
    )
    from backend.app.research_broker_models import (
        ResearchModelOperation,
        ResearchModelTransport,
    )

    operations = list(
        session.scalars(
            select(ResearchModelOperation).filter_by(**exact_scope).limit(4)
        )
    )
    transports = list(
        session.scalars(
            select(ResearchModelTransport).filter_by(**exact_scope).limit(4)
        )
    )
    if (
        {scoped(op, fence).id for op in operations} != {m.operation_id for m in members}
        or len(operations) != len(members)
        or {scoped(t, fence).id for t in transports}
        != {m.transport_id for m in members}
        or len(transports) != len(members)
    ):
        fail()
    expected_exchanges = {c.provider_exchange_id for c in candidates if c.ordinal}
    if (
        {scoped(e, fence).id for e in exchanges} != expected_exchanges
        or len(exchanges) != len(expected_exchanges)
        or {scoped(m, fence).id for m in members}
        != {e.provider_transport_id for e in exchanges}
        or len(members) != len(exchanges)
    ):
        fail()
    actual_units = list(
        session.scalars(
            select(ResearchProcessAdmission)
            .filter_by(
                **{
                    key: getattr(fence, key)
                    for key in ("owner_id", "run_id", "attempt_id", "generation")
                }
            )
            .limit(5)
        )
    )
    expected_units = {admission.id} | {
        result.admission_id for result, _ in outcomes.values()
    }
    if {scoped(row, fence).id for row in actual_units} != expected_units or len(
        actual_units
    ) != len(expected_units):
        fail()
    if len(by_native) != len(candidates):
        fail()
    seed = next((c for c in candidates if c.ordinal == 0), None)
    if seed is None or not outcomes[seed.id][1].eligible:
        fail()
    applications = sorted(
        _rows(session, OEStructuredTerminalApplication, epoch_id),
        key=lambda row: row.ordinal,
    )
    workers = _rows(session, OEStructuredWorkerOutcome, epoch_id)
    if len(applications) != maximum + 1 or len(workers) != maximum:
        fail()
    workers_by_id = {row.id: scoped(row, fence) for row in workers}
    used = set()
    for ordinal, application in enumerate(applications):
        scoped(application, fence)
        cursor, after = controls[2 * ordinal], states[2 * ordinal]
        event = _event(
            application.event_canonical.encode(),
            {"iteration", "outcome", "native_state", "state_sha256"},
        )
        import hashlib

        if (
            application.ordinal != ordinal
            or application.control_state_id != cursor.id
            or hashlib.sha256(application.event_canonical.encode()).hexdigest()
            != application.event_sha256
            or event
            != {
                "iteration": ordinal,
                "outcome": application.outcome,
                "native_state": after,
                "state_sha256": cursor.state_sha256,
            }
        ):
            fail()
        child = seed
        if ordinal:
            worker = workers_by_id.get(application.worker_outcome_id)
            if worker is None or worker.id in used:
                fail()
            used.add(worker.id)
            from backend.app.oe_structured_operational_models import (
                OEStructuredIterationIntent,
            )
            from backend.app.oe_structured_sampling_models import (
                OEStructuredSamplingIntent,
            )

            intent = scoped(
                session.get(OEStructuredIterationIntent, worker.iteration_intent_id),
                fence,
            )
            sampling = scoped(session.get(OEStructuredSamplingIntent, intent.id), fence)
            if (
                intent.epoch_id != epoch_id
                or intent.ordinal != ordinal
                or intent.sampling_state_id != controls[2 * ordinal - 1].id
                or sampling.epoch_id != epoch_id
                or sampling.ordinal != ordinal
                or sampling.sampling_state_id != intent.sampling_state_id
                or sampling.parent_candidate_id != intent.parent_candidate_id
            ):
                fail()
            mutation = _settled(session, fence, sampling.mutation_reservation_id)
            if (
                mutation.purpose != "engine_mutation"
                or mutation.logical_key
                != f"oe-structured-mutation:{epoch_id}:{ordinal}"
                or BudgetVector.model_validate(mutation.amounts)
                != BudgetVector(mutation_slots=1)
            ):
                fail()
            doc = _worker_document(worker, config.search.domain)
            child = by_id.get(worker.candidate_id)
            if (worker.status == "worker_error") != (
                application.outcome == "error"
            ) or doc["projection"]["native_iteration"] != ordinal:
                fail()
            if worker.status == "evaluated":
                if (
                    child is None
                    or child.ordinal != ordinal
                    or child.parent_candidate_id != intent.parent_candidate_id
                ):
                    fail()
                actual, measurement = outcomes[child.id]
                if (
                    not measurement.eligible
                    or (worker.execution_id, worker.engine_outcome_id)
                    != (actual.execution_id, actual.id)
                    or doc["projection"]["objective_ack_id"] != str(actual.id)
                    or doc["observation"]["child"]["metrics"]
                    != {"combined_score": measurement.combined_score}
                ):
                    fail()
        elif application.worker_outcome_id is not None or application.outcome != "seed":
            fail()
        _transition(
            None if ordinal == 0 else states[2 * ordinal - 1],
            after,
            ordinal,
            application.outcome,
            None if child is None else str(child.upstream_program_id),
        )
        if ordinal and child is not None:
            returned = doc["observation"]["child"]
            parent = by_id.get(child.parent_candidate_id)
            if (
                parent is None
                or returned["id"] != str(child.upstream_program_id)
                or returned["code"] != child.candidate_canonical
                or returned["parent_id"] != str(parent.upstream_program_id)
                or doc["observation"]["parent_id"] != str(parent.upstream_program_id)
            ):
                fail()
            saved = after["programs"].get(str(child.upstream_program_id))
            if saved is not None:
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
                    if canonical(saved[key]) != canonical(returned[key]):
                        fail()
        for pid, program in after["programs"].items():
            candidate = by_native.get(pid)
            if candidate is None:
                fail()
            _, measurement = outcomes[candidate.id]
            parent = by_id.get(candidate.parent_candidate_id)
            if (
                program["code"] != candidate.candidate_canonical
                or program["iteration_found"] != candidate.ordinal
                or program["parent_id"]
                != (None if parent is None else str(parent.upstream_program_id))
                or canonical(program["metrics"])
                != canonical(
                    {"combined_score": measurement.combined_score}
                    if measurement.eligible
                    else {}
                )
                or not program["artifacts_json"]
                or _json(program["artifacts_json"])
                != {"structured_feedback": measurement_feedback(measurement)}
            ):
                fail()
    if set(by_id) != {seed.id} | {
        worker.candidate_id for worker in workers if worker.status == "evaluated"
    }:
        fail()
    if len(used) != len(workers):
        fail()
    # Every other obligation must already be positively settled. Upload holds
    # alone are released here, at full conservative charge, after whole stop.
    scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    reservations = list(
        session.scalars(select(ResearchBudgetReservation).filter_by(**scope).limit(257))
    )
    uploads = {upload.reservation_id: upload for _, upload, _ in checkpoints}
    if len(reservations) > 256 or not set(uploads) <= {row.id for row in reservations}:
        fail()
    for row in reservations:
        if row.id not in uploads or row.state == "settled":
            _settled(session, fence, row.id)
        else:
            require_upload_hold(session, fence, uploads[row.id])
    if _stopped_controller(session, fence, epoch_id, settings)[2].id != epoch.id:
        fail()
    for row in reservations:
        if row.id in uploads:
            settle_budget(
                session,
                fence,
                row.id,
                charge=BudgetVector.model_validate(row.amounts),
                outcome="conservative_unknown",
            )
    session.flush()
    return checkpoints[-1][0]
