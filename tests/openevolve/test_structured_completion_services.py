"""Final ACK integrity with SQL/physical-reader doubles, not hosted completion."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_completion_services as service
from packages.research_runtime.process_contracts import ProcessError


@pytest.fixture
def graph(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    epoch = uuid4()
    context = NS(config=NS(search=NS(maximum_mutation_slots=1, domain=object())))
    groups = {
        model: []
        for model in (
            service.OEStructuredCheckpointIntent,
            service.OEStructuredCheckpointUpload,
            service.OEStructuredCheckpointCompletion,
            service.OEStructuredCheckpointPublication,
        )
    }
    cursors = {}
    for ordinal, frontier in enumerate((0, 1, 1), 1):
        cursor = NS(
            id=uuid4(),
            **vars(fence),
            epoch_id=epoch,
            phase="seed" if not frontier else "applied",
            frontier=frontier,
        )
        if ordinal == 3:
            cursor = list(cursors.values())[-1]
        cursors[cursor.id] = cursor
        intent = NS(
            id=uuid4(),
            **vars(fence),
            epoch_id=epoch,
            save_ordinal=ordinal,
            frontier=frontier,
            control_state_id=cursor.id,
            state_sha256=str(frontier),
        )
        upload = NS(id=uuid4(), **vars(fence), epoch_id=epoch, intent_id=intent.id)
        completion = NS(id=uuid4(), **vars(fence), epoch_id=epoch, upload_id=upload.id)
        publication = NS(
            id=uuid4(),
            **vars(fence),
            epoch_id=epoch,
            intent_id=intent.id,
            completion_id=completion.id,
            control_state_id=cursor.id,
            state_sha256=intent.state_sha256,
        )
        for model, row in zip(
            groups, (intent, upload, completion, publication), strict=True
        ):
            groups[model].append(row)
    calls = []
    monkeypatch.setattr(service, "controller", lambda *a: context)
    monkeypatch.setattr(service, "_rows", lambda s, m, e: list(groups[m]))
    monkeypatch.setattr(
        service,
        "retained_files",
        lambda s, f, i, c, settings: calls.append(i.id) or i.frontier,
    )
    monkeypatch.setattr(service, "native_files_state", lambda files, domain: files)
    monkeypatch.setattr(service, "_state", lambda cursor, domain: cursor.frontier)
    monkeypatch.setattr(service, "state_sha256", str)
    monkeypatch.setattr(service, "require_upload_hold", lambda *a: None)
    monkeypatch.setattr(service, "current_state", lambda *a: (cursor, cursor.frontier))
    return NS(
        fence=fence,
        epoch=epoch,
        context=context,
        groups=groups,
        cursors=cursors,
        calls=calls,
        session=NS(get=lambda m, key: cursors[key]),
    )


def ready(g):
    return service.is_final_checkpoint_ready(
        g.session, g.fence, g.epoch, NS(), peer=object()
    )


def test_final_ack_requires_every_actual_inventory(graph):
    assert ready(graph) is True
    assert len(graph.calls) == 3


def test_intermediate_is_not_final_ack(graph):
    graph.groups[service.OEStructuredCheckpointIntent].pop()
    assert ready(graph) is False
    assert not graph.calls


@pytest.mark.parametrize(
    "change",
    [
        "missing_publication",
        "orphan_upload",
        "wrong_scope",
        "wrong_digest",
        "wrong_cursor",
        "noncontiguous",
        "missing_completion",
    ],
)
def test_final_graph_must_close_bidirectionally(graph, change):
    g = graph
    if change == "missing_publication":
        g.groups[service.OEStructuredCheckpointPublication].pop()
    elif change == "missing_completion":
        g.groups[service.OEStructuredCheckpointCompletion].pop()
    elif change == "orphan_upload":
        g.groups[service.OEStructuredCheckpointUpload][-1].intent_id = uuid4()
    elif change == "wrong_scope":
        g.groups[service.OEStructuredCheckpointPublication][-1].owner_id = uuid4()
    elif change == "wrong_digest":
        g.groups[service.OEStructuredCheckpointIntent][-1].state_sha256 = "bad"
    elif change == "wrong_cursor":
        g.groups[service.OEStructuredCheckpointIntent][-1].frontier = 0
    else:
        g.groups[service.OEStructuredCheckpointIntent][-1].save_ordinal = 4
    with pytest.raises(ProcessError):
        ready(g)


def test_actual_byte_failure_does_not_ack(graph, monkeypatch):
    monkeypatch.setattr(service, "retained_files", lambda *a: service.fail())
    with pytest.raises(ProcessError):
        ready(graph)


def test_fresh_terminal_failure_does_not_ack(graph, monkeypatch):
    monkeypatch.setattr(service, "current_state", lambda *a: service.fail())
    with pytest.raises(ProcessError):
        ready(graph)


@pytest.fixture
def stopped_case(monkeypatch):
    import hashlib
    from copy import deepcopy

    from backend.app import oe_structured_population, research_broker_services
    from backend.app.oe_structured_models import (
        OEStructuredCandidate,
        OEStructuredProviderExchange,
    )
    from backend.app.oe_structured_operational_models import (
        OEStructuredIterationIntent,
        OEStructuredProviderTransport,
    )
    from backend.app.oe_structured_sampling_models import OEStructuredSamplingIntent
    from backend.app.oe_structured_terminal_models import (
        OEStructuredTerminalApplication,
        OEStructuredWorkerOutcome,
    )
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchModelOperation,
        ResearchModelTransport,
    )
    from backend.app.research_process_models import ResearchProcessAdmission
    from packages.openevolve_structured.checkpoint import canonical, state_sha256
    from packages.research_runtime.budget_contracts import BudgetVector

    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    fence, epoch = NS(**scope), NS(id=uuid4())
    config = NS(search=NS(maximum_mutation_slots=1, domain=object()))
    candidate = NS(
        id=uuid4(),
        **scope,
        epoch_id=epoch.id,
        ordinal=0,
        upstream_program_id=uuid4(),
        parent_candidate_id=None,
        candidate_canonical="{}",
    )
    program = {
        "code": "{}",
        "iteration_found": 0,
        "parent_id": None,
        "metrics": {"combined_score": -5.0},
        "artifacts_json": '{"structured_feedback":"verified-double"}',
    }
    before = {
        "metadata": {"last_iteration": 0},
        "programs": {str(candidate.upstream_program_id): program},
    }
    after = deepcopy(before)
    after["metadata"]["last_iteration"] = 1
    controls, states = [], [before, before, after]
    for index, state in enumerate(states, 1):
        controls.append(
            NS(
                id=uuid4(),
                **scope,
                epoch_id=epoch.id,
                sequence=index,
                previous_id=None if index == 1 else controls[-1].id,
                phase=("seed", "sampling", "applied")[index - 1],
                frontier=(index - 1) // 2,
                state_sha256=state_sha256(state),
            )
        )
    intent = NS(
        id=uuid4(),
        **scope,
        epoch_id=epoch.id,
        ordinal=1,
        sampling_state_id=controls[1].id,
        parent_candidate_id=candidate.id,
    )
    mutation = NS(
        id=uuid4(),
        **scope,
        purpose="engine_mutation",
        logical_key=f"oe-structured-mutation:{epoch.id}:1",
        state="settled",
        amounts=BudgetVector(mutation_slots=1).model_dump(),
    )
    sampling = NS(
        id=intent.id,
        **scope,
        epoch_id=epoch.id,
        ordinal=1,
        sampling_state_id=controls[1].id,
        parent_candidate_id=candidate.id,
        mutation_reservation_id=mutation.id,
    )
    worker = NS(
        id=uuid4(),
        **scope,
        epoch_id=epoch.id,
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
            "epoch_id": str(epoch.id),
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
    worker.evidence_sha256 = hashlib.sha256(canonical(doc)).hexdigest()
    applications = []
    for ordinal, state in enumerate((before, after)):
        event = {
            "iteration": ordinal,
            "outcome": "seed" if ordinal == 0 else "error",
            "native_state": state,
            "state_sha256": state_sha256(state),
        }
        raw = canonical(event)
        applications.append(
            NS(
                id=uuid4(),
                **scope,
                epoch_id=epoch.id,
                ordinal=ordinal,
                control_state_id=controls[ordinal * 2].id,
                worker_outcome_id=None if ordinal == 0 else worker.id,
                outcome=event["outcome"],
                event_canonical=raw.decode(),
                event_sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    admission = NS(id=uuid4(), **scope)
    engine = NS(id=uuid4(), admission_id=uuid4())
    units = [admission, NS(id=engine.admission_id, **scope)]
    holds = [
        NS(
            id=uuid4(),
            **scope,
            state="reserved",
            amounts=BudgetVector(storage_bytes=1).model_dump(),
        )
        for _ in range(3)
    ]
    checkpoints = [
        (
            NS(id=uuid4(), frontier=0 if i == 0 else 1),
            NS(reservation_id=h.id),
            controls[0 if i == 0 else 2],
        )
        for i, h in enumerate(holds)
    ]
    rows = {
        service.OEStructuredControlState: controls,
        OEStructuredCandidate: [candidate],
        OEStructuredProviderExchange: [],
        OEStructuredProviderTransport: [],
        OEStructuredTerminalApplication: applications,
        OEStructuredWorkerOutcome: [worker],
        ResearchProcessAdmission: units,
        ResearchModelOperation: [],
        ResearchModelTransport: [],
        ResearchBudgetReservation: holds + [mutation],
    }
    writes = []
    session = NS(
        scalars=lambda q: rows[q.column_descriptions[0]["entity"]],
        get=lambda m, k: {
            OEStructuredIterationIntent: intent,
            OEStructuredSamplingIntent: sampling,
        }[m],
        flush=lambda: writes.append("flush"),
    )
    monkeypatch.setattr(service, "_rows", lambda s, m, e: list(rows[m]))
    monkeypatch.setattr(
        service,
        "_stopped_controller",
        lambda *a: (NS(), config, epoch, NS(), admission),
    )
    monkeypatch.setattr(service, "_published_checkpoints", lambda *a: checkpoints)
    monkeypatch.setattr(service, "_state", lambda c, d: states[c.sequence - 1])
    monkeypatch.setattr(
        service,
        "_stopped_engine",
        lambda *a: (engine, NS(eligible=True, combined_score=-5.0)),
    )
    monkeypatch.setattr(service, "_settled", lambda s, f, r: mutation)
    monkeypatch.setattr(service, "require_upload_hold", lambda *a: None)
    monkeypatch.setattr(
        oe_structured_population, "measurement_feedback", lambda m: "verified-double"
    )
    monkeypatch.setattr(
        research_broker_services,
        "settle_budget",
        lambda *a, **k: writes.append((a[2], k)),
    )
    return NS(
        session=session,
        fence=fence,
        epoch=epoch,
        settings=NS(),
        checkpoints=checkpoints,
        controls=controls,
        applications=applications,
        worker=worker,
        holds=holds,
        units=units,
        rows=rows,
        writes=writes,
    )


def test_stopped_error_frontier_retains_seed_no_invented_child_and_settles_uploads(
    stopped_case,
):
    t = stopped_case
    assert (
        service.complete_stopped_epoch(t.session, t.fence, t.epoch.id, t.settings)
        is t.checkpoints[-1][0]
    )
    assert len(t.writes) == 4 and t.writes[-1] == "flush"
    assert {item[0] for item in t.writes[:-1]} == {h.id for h in t.holds}
    assert all(item[1]["outcome"] == "conservative_unknown" for item in t.writes[:-1])


@pytest.mark.parametrize(
    "change",
    [
        "missing_stop",
        "missing_engine",
        "bad_event",
        "wrong_predecessor",
        "orphan_unit",
        "missing_checkpoint",
        "unsettled",
    ],
)
def test_poststop_never_settles_uploads_on_partial_graph(
    stopped_case, monkeypatch, change
):
    t = stopped_case
    if change == "missing_stop":
        monkeypatch.setattr(service, "_stopped_controller", lambda *a: service.fail())
    elif change == "missing_engine":
        monkeypatch.setattr(service, "_stopped_engine", lambda *a: service.fail())
    elif change == "bad_event":
        t.applications[-1].event_sha256 = "b" * 64
    elif change == "wrong_predecessor":
        t.controls[-1].previous_id = uuid4()
    elif change == "orphan_unit":
        t.units.append(NS(id=uuid4(), **vars(t.fence)))
    elif change == "missing_checkpoint":
        t.checkpoints.pop()
    else:
        monkeypatch.setattr(service, "_settled", lambda *a: service.fail())
    with pytest.raises(ProcessError):
        service.complete_stopped_epoch(t.session, t.fence, t.epoch.id, t.settings)
    assert not t.writes


@pytest.mark.parametrize(
    "change", [None, "stop_only", "missing_file", "different_receipt"]
)
def test_stopped_controller_requires_actual_durable_completion(monkeypatch, change):
    from backend.app import factor_native_io, oe_structured_identity, research_lifecycle
    from backend.app import oe_structured_controller_process_services as process
    from backend.app import oe_structured_epoch_services as epoch_service
    from runtimes.openevolve_structured_host import completion_journal, process_journal

    fence = NS()
    epoch = NS(id=uuid4(), controller_admission_id=uuid4())
    admission = NS(reservation_id=uuid4())
    config = object()
    run = object()

    def document(value):
        return NS(model_dump=lambda **k: {"value": value})

    intent, identity, child, stop, completion = [document(i) for i in range(5)]
    records = {
        key: NS(payload=value.model_dump())
        for key, value in zip(
            (
                "launch_intent",
                "launch_identity",
                "controller_child",
                "stop",
                "controller_completion",
            ),
            (intent, identity, child, stop, completion),
            strict=True,
        )
    }
    if change == "stop_only":
        records.pop("controller_completion")
    elif change == "different_receipt":
        records["controller_completion"].payload = {"value": "other"}
    monkeypatch.setattr(
        research_lifecycle, "require_neutral_transaction", lambda *a, **k: (run, None)
    )
    monkeypatch.setattr(
        factor_native_io, "require_native_storage_guard", lambda *a: None
    )
    monkeypatch.setattr(
        epoch_service,
        "expected_controller_bootstrap",
        lambda *a: (epoch, object(), b""),
    )
    monkeypatch.setattr(
        oe_structured_identity, "decode_spec", lambda *a: (None, config)
    )
    monkeypatch.setattr(
        process,
        "_persisted",
        lambda *a: (admission, NS(status="stopped"), records, intent),
    )
    monkeypatch.setattr(process, "_expected", lambda *a: None)
    monkeypatch.setattr(
        process_journal,
        "read_controller_journal",
        lambda *a, **k: (NS(intent=intent), identity, child, stop),
    )
    monkeypatch.setattr(
        completion_journal,
        "read_controller_completion",
        lambda *a: service.fail() if change == "missing_file" else completion,
    )
    monkeypatch.setattr(service, "_settled", lambda *a: None)
    if change:
        with pytest.raises(ProcessError):
            service._stopped_controller(
                None, fence, epoch.id, NS(storage_root="/unused")
            )
    else:
        assert service._stopped_controller(
            None, fence, epoch.id, NS(storage_root="/unused")
        )[:3] == (run, config, epoch)
