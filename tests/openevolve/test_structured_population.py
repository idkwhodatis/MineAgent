"""Native state byte checks with DB/peer doubles; not actual hosted selection."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_population as service
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.checkpoint import (
    canonical,
    state_sha256,
    validate_directory,
)
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401


@pytest.fixture
def graph(native, domain):  # noqa: F811
    state = validate_directory(native, domain)
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    epoch_id = uuid4()
    first = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch_id,
        previous_id=None,
        sequence=1,
        frontier=0,
        phase="seed",
        state_canonical=canonical(state).decode(),
        state_sha256=state_sha256(state),
    )
    second = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch_id,
        previous_id=first.id,
        sequence=2,
        frontier=0,
        phase="sampling",
        state_canonical=canonical(state).decode(),
        state_sha256=state_sha256(state),
    )
    intent = NS(
        **scope, id=uuid4(), epoch_id=epoch_id, sampling_state_id=second.id, ordinal=1
    )
    return NS(
        state=state,
        rows=[first, second],
        intent=intent,
        fence=NS(**scope),
        domain=domain,
    )


def test_actual_sampling_preserves_population_and_applied_frontier(graph):
    before, after = service._chain(graph.rows, graph.fence, graph.intent, graph.domain)
    assert before == after == graph.state


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "sequence",
        "previous",
        "epoch",
        "scope",
        "frontier",
        "phase",
        "hash",
        "canonical",
        "stale",
    ],
)
def test_control_chain_rejects_relinked_or_stale_state(graph, change):
    row = graph.rows[-1]
    if change == "missing":
        graph.rows.pop(0)
    elif change == "sequence":
        row.sequence = 4
    elif change == "previous":
        row.previous_id = uuid4()
    elif change == "epoch":
        row.epoch_id = uuid4()
    elif change == "scope":
        row.attempt_id = uuid4()
    elif change == "frontier":
        row.frontier = 1
    elif change == "phase":
        row.phase = "applied"
    elif change == "hash":
        row.state_sha256 = "0" * 64
    elif change == "canonical":
        row.state_canonical += " "
    else:
        graph.intent.sampling_state_id = uuid4()
    with pytest.raises((ProcessError, ValueError)):
        service._chain(graph.rows, graph.fence, graph.intent, graph.domain)


def test_sampling_cannot_rewrite_a_program_even_with_new_valid_hash(graph):
    changed = deepcopy(graph.state)
    next(iter(changed["programs"].values()))["metrics"]["combined_score"] = 500.0
    graph.rows[-1].state_canonical = canonical(changed).decode()
    graph.rows[-1].state_sha256 = state_sha256(changed)
    with pytest.raises(ProcessError, match="sampling_changed"):
        service._chain(graph.rows, graph.fence, graph.intent, graph.domain)


def selection(graph):
    pid = next(iter(graph.state["programs"]))
    value = {
        "schema_version": "mineagent.oe-structured-selection.v1",
        "native_iteration": 1,
        "parent_id": pid,
        "inspiration_ids": [pid],
        "snapshot": {
            "programs": graph.state["programs"],
            "islands": graph.state["metadata"]["islands"],
            "current_island": 0,
            "sampling_island": 0,
            "feature_dimensions": ["complexity", "diversity"],
            "artifacts": {},
        },
    }
    return value, NS(upstream_program_id=pid)


def test_exact_six_field_native_selection_matches_before_state(graph):
    value, parent = selection(graph)
    graph.intent.selection_canonical = canonical_bytes(value).decode()
    graph.intent.selection_sha256 = digest(value)
    service._selection(graph.intent, graph.state, parent)


@pytest.mark.parametrize(
    "change", ["score", "artifact", "parent", "island", "inspiration", "extra"]
)
def test_self_consistent_selection_hash_does_not_authorize_changed_snapshot(
    graph, change
):
    value, parent = selection(graph)
    value = deepcopy(value)
    snapshot = value["snapshot"]
    if change == "score":
        next(iter(snapshot["programs"].values()))["metrics"]["combined_score"] = 123.0
    elif change == "artifact":
        snapshot["artifacts"] = {"fake": {"score": 123}}
    elif change == "parent":
        value["parent_id"] = str(uuid4())
    elif change == "island":
        snapshot["current_island"] = False
    elif change == "inspiration":
        value["inspiration_ids"] = [str(uuid4())]
    else:
        snapshot["approved"] = True
    graph.intent.selection_canonical = canonical_bytes(value).decode()
    graph.intent.selection_sha256 = digest(value)
    with pytest.raises((ProcessError, ValueError)):
        service._selection(graph.intent, graph.state, parent)


def reader(graph, monkeypatch):
    import sys

    from packages.openevolve_structured.objective import StructuredMeasurement

    pid = next(iter(graph.state["programs"]))
    candidate = NS(
        **vars(graph.fence),
        id=uuid4(),
        epoch_id=graph.intent.epoch_id,
        upstream_program_id=pid,
        ordinal=0,
        parent_candidate_id=None,
        candidate_canonical=graph.state["programs"][pid]["code"],
        candidate_sha256=hashlib.sha256(
            graph.state["programs"][pid]["code"].encode()
        ).hexdigest(),
        program_version_id=uuid4(),
    )
    graph.intent.parent_candidate_id = candidate.id
    measurement = StructuredMeasurement(
        execution_id=uuid4(),
        structured_strategy_version_id=candidate.program_version_id,
        snapshot_id=uuid4(),
        strategy_input_sha256="a" * 64,
        market_sha256="b" * 64,
        result_sha256="c" * 64,
        objective_sha256="d" * 64,
        total_return_pct=-4.0,
        max_drawdown_pct=1.0,
        closed_trade_count=0,
        eligible=True,
        combined_score=-5.0,
        reason="eligible",
    )
    graph.state["programs"][pid]["artifacts_json"] = canonical(
        {"structured_feedback": service.measurement_feedback(measurement)}
    ).decode()
    for row in graph.rows:
        row.state_canonical = canonical(graph.state).decode()
        row.state_sha256 = state_sha256(graph.state)
    value, _ = selection(graph)
    value["snapshot"]["artifacts"] = {
        pid: {"structured_feedback": service.measurement_feedback(measurement)}
    }
    graph.intent.selection_canonical = canonical_bytes(value).decode()
    graph.intent.selection_sha256 = digest(value)
    values = iter([graph.rows, [candidate]])
    session = NS(
        get=lambda *a: graph.intent,
        scalars=lambda *a: next(values),
        scalar=lambda *a: graph.rows[-1],
    )
    config = NS(search=NS(domain=graph.domain))
    peer, calls = object(), []

    def context(*args, **kwargs):
        assert kwargs["peer"] is peer and kwargs["intent_id"] == graph.intent.id
        calls.append("current_peer")
        return NS(config=config)

    monkeypatch.setitem(
        sys.modules,
        "backend.app.oe_structured_controller_context",
        NS(require_controller_context=context),
    )
    monkeypatch.setattr(service, "require_neutral_transaction", lambda *a, **k: None)
    verified = NS(candidate_id=candidate.id, measurement=measurement)

    def actual_result(*args, **kwargs):
        assert args[2] == candidate.id
        assert kwargs == {"peer": peer, "current_intent_id": graph.intent.id}
        calls.append("physical_member")
        return verified

    monkeypatch.setattr(service, "read_population_engine_outcome", actual_result)
    return NS(**locals())


def test_selection_rechecks_peer_and_actual_member_result(graph, monkeypatch):
    f = reader(graph, monkeypatch)
    service.require_selection(
        f.session, graph.fence, graph.intent, f.config, None, peer=f.peer
    )
    assert f.calls == ["current_peer", "physical_member", "current_peer"]


def test_native_score_must_equal_fresh_physical_measurement(graph, monkeypatch):
    f = reader(graph, monkeypatch)
    f.verified.measurement = f.measurement.model_copy(update={"combined_score": 100.0})
    with pytest.raises(ProcessError, match="population_accounting"):
        service.require_selection(
            f.session, graph.fence, graph.intent, f.config, None, peer=f.peer
        )


def test_changed_latest_cursor_after_physical_read_rejects_old_worker(
    graph, monkeypatch
):
    f = reader(graph, monkeypatch)
    f.session.scalar = lambda *a: NS(id=uuid4())
    with pytest.raises(ProcessError, match="stale_worker"):
        service.require_selection(
            f.session, graph.fence, graph.intent, f.config, None, peer=f.peer
        )
