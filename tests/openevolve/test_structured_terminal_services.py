"""Terminal byte/transition tests; no physical engine or PG claims."""

from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_terminal_services as service
from packages.openevolve_structured.checkpoint import canonical, validate_directory
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401


@pytest.fixture
def state(native, domain):  # noqa: F811
    return validate_directory(native, domain)


def test_seed_contains_only_actual_seed(state):
    pid = next(iter(state["programs"]))
    service._transition(None, state, 0, "seed", pid)
    with pytest.raises(ProcessError):
        service._transition(None, state, 0, "seed", str(uuid4()))


@pytest.mark.parametrize("outcome", ["error", "rejected"])
def test_nonaccepted_only_advances_frontier(state, outcome):
    after = deepcopy(state)
    after["metadata"]["last_iteration"] = 1
    service._transition(state, after, 1, outcome, None)
    after["metadata"]["best_program_id"] = None
    with pytest.raises(ProcessError):
        service._transition(state, after, 1, outcome, None)


@pytest.mark.parametrize(
    "change", ["old_score", "old_code", "missing_old", "wrong_child", "missing_child"]
)
def test_acceptance_cannot_rewrite_or_substitute_population(state, change):
    pid = next(iter(state["programs"]))
    cid = str(uuid4())
    after = deepcopy(state)
    after["metadata"]["last_iteration"] = 1
    child = deepcopy(state["programs"][pid])
    child["id"] = cid
    after["programs"][cid] = child
    service._transition(state, after, 1, "accepted", cid)
    if change == "old_score":
        after["programs"][pid]["metrics"]["combined_score"] = 999
    elif change == "old_code":
        after["programs"][pid]["code"] += " "
    elif change == "missing_old":
        del after["programs"][pid]
    elif change == "wrong_child":
        after["programs"][str(uuid4())] = after["programs"].pop(cid)
    else:
        del after["programs"][cid]
    with pytest.raises(ProcessError):
        service._transition(state, after, 1, "accepted", cid)


@pytest.mark.parametrize(
    "raw", [b'{"a":1,"a":1}', b'{ "a":1}', b'{"a":NaN}', b'{"a":1,"b":2}']
)
def test_original_event_is_closed_duplicate_free_canonical(raw):
    with pytest.raises((ProcessError, ValueError)):
        service._event(raw, {"a"})


def test_error_evidence_preserves_no_child_and_actual_event_digest(domain):  # noqa: F811
    epoch, intent = uuid4(), uuid4()
    value = {
        "schema_version": "mineagent.oe-structured-worker-outcome.v1",
        "projection": {
            "epoch_id": str(epoch),
            "intent_id": str(intent),
            "native_iteration": 1,
            "status": "worker_error",
            "objective_ack_id": None,
        },
        "observation": {
            "iteration": 1,
            "outcome": "worker_error",
            "child": None,
            "parent_id": None,
            "target_island": None,
        },
        "event_sha256": "a" * 64,
    }
    raw = canonical(value)
    row = NS(
        epoch_id=epoch,
        iteration_intent_id=intent,
        status="worker_error",
        evidence_canonical=raw.decode(),
        evidence_sha256=service._digest(raw),
        event_sha256="a" * 64,
        candidate_id=None,
        execution_id=None,
        engine_outcome_id=None,
    )
    assert service._worker_document(row, domain) == value
    row.event_sha256 = "b" * 64
    with pytest.raises(ProcessError):
        service._worker_document(row, domain)


from tests.openevolve.test_structured_sampling_authority import case  # noqa: F401


@pytest.fixture
def terminal_case(case, monkeypatch):  # noqa: F811
    from backend.app.oe_structured_operational_models import OEStructuredControlState
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
    )
    from packages.openevolve_structured.checkpoint import _json, state_sha256

    state = _json(case.control.state_canonical)
    pid = str(case.candidate.upstream_program_id)
    state["programs"][pid]["artifacts_json"] = '{"structured_feedback":"actual-double"}'
    event = {
        "iteration": 0,
        "outcome": "seed",
        "native_state": state,
        "state_sha256": state_sha256(state),
    }
    records = []
    cursor = [None]
    previous = [None]
    reads = []

    def scalar(stmt):
        cls = stmt.column_descriptions[0]["entity"]
        if cls is OEStructuredTerminalApplication:
            return previous[0]
        if cls is OEStructuredControlState:
            return cursor[0]
        raise AssertionError(cls)

    session = NS(
        scalar=scalar,
        scalars=lambda stmt: [case.candidate],
        add=records.append,
        flush=lambda: records.append("flush"),
    )
    measurement = NS(
        eligible=True,
        combined_score=-5.0,
        structured_strategy_version_id=case.candidate.program_version_id,
    )

    def physical(*args, peer, current_control_id):
        reads.append(current_control_id)
        return NS(measurement=measurement)

    monkeypatch.setattr(
        service, "require_controller_context", lambda *a, **k: case.context
    )
    monkeypatch.setattr(service, "read_terminal_engine_outcome", physical)
    monkeypatch.setattr(service, "measurement_feedback", lambda value: "actual-double")
    return NS(
        c=case,
        session=session,
        event=event,
        records=records,
        cursor=cursor,
        previous=previous,
        reads=reads,
        measurement=measurement,
    )


def publish(t):
    return service.register_terminal_application(
        t.session,
        t.c.fence,
        t.c.candidate.epoch_id,
        canonical(t.event),
        t.c.settings,
        peer=t.c.peer,
    )


def test_seed_publication_and_exact_replay_reread_physical_no_second_write(
    terminal_case,
):
    t = terminal_case
    row = publish(t)
    assert len(t.records) == 3 and t.records[-1] == "flush"
    assert row.worker_outcome_id is None and row.outcome == "seed"
    assert t.reads == [None]
    t.previous[0], t.cursor[0] = row, t.records[0]
    assert publish(t) is row
    assert len(t.records) == 3
    assert t.reads == [None, t.cursor[0].id]


@pytest.mark.parametrize(
    "change",
    ["ineligible", "score", "artifact", "lost_stop", "cursor_drift", "replay_body"],
)
def test_seed_never_publishes_without_actual_consistent_result(
    terminal_case, monkeypatch, change
):
    from packages.openevolve_structured.checkpoint import state_sha256

    t = terminal_case
    program = next(iter(t.event["native_state"]["programs"].values()))
    if change == "ineligible":
        t.measurement.eligible = False
    elif change == "score":
        program["metrics"]["combined_score"] = 100
    elif change == "artifact":
        program["artifacts_json"] = '{"structured_feedback":"caller prose"}'
    elif change == "lost_stop":

        def missing(*a, **k):
            raise ProcessError("missing_positive_stop")

        monkeypatch.setattr(service, "read_terminal_engine_outcome", missing)
    elif change == "cursor_drift":

        def replaced(*a, **k):
            t.cursor[0] = NS(id=uuid4())
            return NS(measurement=t.measurement)

        monkeypatch.setattr(service, "read_terminal_engine_outcome", replaced)
    else:
        row = publish(t)
        t.previous[0], t.cursor[0] = row, t.records[0]
        program["timestamp"] = 99
    t.event["state_sha256"] = state_sha256(t.event["native_state"])
    count = len(t.records)
    with pytest.raises(ProcessError):
        publish(t)
    assert len(t.records) == count


@pytest.fixture
def error_case(terminal_case, monkeypatch):
    from backend.app import research_broker_services
    from backend.app.oe_structured_operational_models import OEStructuredIterationIntent
    from backend.app.oe_structured_sampling_models import OEStructuredSamplingIntent
    from backend.app.oe_structured_terminal_models import OEStructuredWorkerOutcome
    from backend.app.research_broker_models import ResearchBudgetReservation
    from packages.openevolve_structured.checkpoint import state_sha256
    from packages.research_runtime.budget_contracts import BudgetVector

    t = terminal_case
    scope = vars(t.c.fence)
    baseline = NS(**vars(t.c.control))
    baseline.phase, baseline.sequence = "sampling", 2
    baseline.state_canonical = canonical(t.event["native_state"]).decode()
    baseline.state_sha256 = state_sha256(t.event["native_state"])
    t.cursor[0] = baseline
    intent = NS(
        **scope,
        id=uuid4(),
        epoch_id=t.c.candidate.epoch_id,
        ordinal=1,
        sampling_state_id=baseline.id,
        parent_candidate_id=t.c.candidate.id,
    )
    worker = NS(
        **scope,
        id=uuid4(),
        epoch_id=intent.epoch_id,
        iteration_intent_id=intent.id,
        status="worker_error",
        candidate_id=None,
        execution_id=None,
        engine_outcome_id=None,
        event_sha256="a" * 64,
    )
    doc = {
        "schema_version": "mineagent.oe-structured-worker-outcome.v1",
        "projection": {
            "epoch_id": str(intent.epoch_id),
            "intent_id": str(intent.id),
            "native_iteration": 1,
            "status": "worker_error",
            "objective_ack_id": None,
        },
        "observation": {
            "iteration": 1,
            "outcome": "worker_error",
            "child": None,
            "parent_id": None,
            "target_island": None,
        },
        "event_sha256": worker.event_sha256,
    }
    worker.evidence_canonical = canonical(doc).decode()
    worker.evidence_sha256 = service._digest(canonical(doc))
    reservation = NS(
        **scope,
        id=uuid4(),
        purpose="engine_mutation",
        logical_key=f"oe-structured-mutation:{intent.epoch_id}:1",
        amounts=BudgetVector(mutation_slots=1).model_dump(),
        state="reserved",
    )
    sampling = NS(**scope, id=intent.id, mutation_reservation_id=reservation.id)
    original_scalar = t.session.scalar

    def query(stmt):
        cls = stmt.column_descriptions[0]["entity"]
        if cls is OEStructuredIterationIntent:
            return intent
        if cls is OEStructuredWorkerOutcome:
            return worker
        return original_scalar(stmt)

    t.session.scalar = query
    t.session.get = lambda cls, key: {
        (OEStructuredSamplingIntent, sampling.id): sampling,
        (ResearchBudgetReservation, reservation.id): reservation,
    }.get((cls, key))
    settlements = []
    monkeypatch.setattr(
        research_broker_services,
        "settle_budget",
        lambda *a, **k: settlements.append((a[2], k)),
    )
    t.event["iteration"], t.event["outcome"] = 1, "error"
    t.event["native_state"]["metadata"]["last_iteration"] = 1
    t.event["state_sha256"] = state_sha256(t.event["native_state"])
    t.worker, t.reservation, t.settlements = worker, reservation, settlements
    return t


def test_error_application_preserves_population_and_fully_charges_mutation_only(
    error_case,
):
    from packages.research_runtime.budget_contracts import BudgetVector

    t = error_case
    row = publish(t)
    assert row.outcome == "error" and row.worker_outcome_id == t.worker.id
    assert t.records[0].frontier == 1 and t.records[0].phase == "applied"
    assert t.settlements == [
        (
            t.reservation.id,
            {
                "charge": BudgetVector(mutation_slots=1),
                "outcome": "conservative_unknown",
            },
        )
    ]


@pytest.mark.parametrize(
    "change",
    ["hold", "logical_key", "worker_scope", "invented_outcome", "changed_state"],
)
def test_error_application_cannot_invent_result_or_release_other_hold(
    error_case, change
):
    from packages.openevolve_structured.checkpoint import state_sha256
    from packages.research_runtime.budget_contracts import BudgetVector

    t = error_case
    if change == "hold":
        t.reservation.amounts = BudgetVector(mutation_slots=2).model_dump()
    elif change == "logical_key":
        t.reservation.logical_key += "-wrong"
    elif change == "worker_scope":
        t.worker.owner_id = uuid4()
    elif change == "invented_outcome":
        t.event["outcome"] = "accepted"
    else:
        t.event["native_state"]["metadata"]["feature_stats"] = {"caller": 1}
    t.event["state_sha256"] = state_sha256(t.event["native_state"])
    with pytest.raises(ProcessError):
        publish(t)
    assert not t.records and not t.settlements


@pytest.mark.parametrize(
    "change", [None, "iteration", "invented_child", "descriptor_drift"]
)
def test_original_worker_error_is_retained_once_without_engine_score(
    case,  # noqa: F811
    monkeypatch,
    change,
):
    from backend.app import oe_structured_worker_descriptor as descriptor
    from backend.app.oe_structured_terminal_models import OEStructuredWorkerOutcome

    intent = NS(
        **vars(case.fence), id=uuid4(), epoch_id=case.candidate.epoch_id, ordinal=1
    )
    records, calls = [], []
    previous = [None]

    def require(*a, **k):
        calls.append(k["peer"])
        return (
            NS(id=uuid4())
            if change == "descriptor_drift" and len(calls) == 2
            else intent
        )

    monkeypatch.setattr(descriptor, "require_worker_descriptor", require)
    monkeypatch.setattr(
        service, "require_controller_context", lambda *a, **k: case.context
    )
    monkeypatch.setattr(
        service,
        "read_candidate_engine_outcome",
        lambda *a, **k: pytest.fail("error fabricated engine result"),
    )
    session = NS(
        get=lambda cls, key: intent,
        scalar=lambda stmt: previous[0],
        add=records.append,
        flush=lambda: records.append("flush"),
    )
    event = {
        "grant": {"actual_descriptor_double": True},
        "iteration": 1,
        "outcome": "worker_error",
        "child": None,
        "parent_id": None,
        "target_island": None,
    }
    if change == "iteration":
        event["iteration"] = 2
    elif change == "invented_child":
        event["child"] = {"metrics": {"combined_score": 10}}
    raw = canonical(event)
    if change:
        with pytest.raises((ValueError, ProcessError)):
            service.register_worker_outcome(
                session, case.fence, raw, case.settings, peer=case.peer
            )
        assert not records
        return
    row = service.register_worker_outcome(
        session, case.fence, raw, case.settings, peer=case.peer
    )
    assert type(row) is OEStructuredWorkerOutcome
    assert row.candidate_id is row.engine_outcome_id is row.execution_id is None
    assert row.event_sha256 == service._digest(raw)
    previous[0] = row
    assert (
        service.register_worker_outcome(
            session, case.fence, raw, case.settings, peer=case.peer
        )
        is row
    )
    assert records == [row, "flush"]


@pytest.mark.parametrize("change", [None, "wrong_ack", "false_score", "wrong_child"])
def test_accepted_application_uses_exact_worker_and_actual_engine_outcome(
    error_case, monkeypatch, change
):
    from backend.app.oe_structured_models import OEStructuredEngineOutcome
    from packages.openevolve_structured.checkpoint import _json, state_sha256

    t = error_case
    seed = t.c.candidate
    child = NS(**vars(seed))
    child.id, child.upstream_program_id, child.program_version_id = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    child.ordinal, child.parent_candidate_id = 1, seed.id
    engine = NS(
        **vars(t.c.fence), id=uuid4(), candidate_id=child.id, execution_id=uuid4()
    )
    t.worker.status, t.worker.candidate_id = "evaluated", child.id
    t.worker.execution_id, t.worker.engine_outcome_id = engine.execution_id, engine.id
    program = deepcopy(next(iter(t.event["native_state"]["programs"].values())))
    program.update(
        id=str(child.upstream_program_id),
        parent_id=str(seed.upstream_program_id),
        generation=1,
        iteration_found=1,
        artifacts_json=None,
    )
    document = _json(t.worker.evidence_canonical)
    document["projection"].update(status="evaluated", objective_ack_id=str(engine.id))
    document["observation"].update(
        outcome="evaluated",
        child=deepcopy(program),
        parent_id=str(seed.upstream_program_id),
        target_island=0,
    )
    if change == "wrong_ack":
        document["projection"]["objective_ack_id"] = str(uuid4())
    t.worker.evidence_canonical = canonical(document).decode()
    t.worker.evidence_sha256 = service._digest(canonical(document))
    t.session.scalars = lambda stmt: [seed, child]
    original_get = t.session.get
    t.session.get = lambda cls, key: (
        engine
        if cls is OEStructuredEngineOutcome and key == engine.id
        else original_get(cls, key)
    )

    def read(*args, **kwargs):
        candidate = child if args[2] == child.id else seed
        return NS(
            execution_id=engine.execution_id,
            measurement=NS(
                eligible=True,
                combined_score=-5.0,
                structured_strategy_version_id=candidate.program_version_id,
            ),
        )

    monkeypatch.setattr(service, "read_terminal_engine_outcome", read)
    program["artifacts_json"] = '{"structured_feedback":"actual-double"}'
    if change == "false_score":
        program["metrics"]["combined_score"] = 12.0
    if change == "wrong_child":
        program["timestamp"] = 42.0
    t.event["outcome"] = "accepted"
    t.event["native_state"]["programs"][str(child.upstream_program_id)] = program
    t.event["state_sha256"] = state_sha256(t.event["native_state"])
    if change:
        with pytest.raises(ProcessError):
            publish(t)
        assert not t.records and not t.settlements
    else:
        row = publish(t)
        assert row.outcome == "accepted" and row.worker_outcome_id == t.worker.id
        assert len(t.settlements) == 1
