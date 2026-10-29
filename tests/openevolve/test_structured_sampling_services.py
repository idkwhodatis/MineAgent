"""Real sampling DTO/state grammar with explicit DB/engine-observation doubles."""

from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app import oe_structured_sampling_services as service
from packages.openevolve_structured.checkpoint import canonical, state_sha256
from packages.openevolve_structured.objective import StructuredMeasurement
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import ControllerBinding
from runtimes.rdagent_operational.peer import KernelIdentity
from tests.openevolve.test_structured_population import domain, graph, native, selection  # noqa: F401


@pytest.fixture
def scenario(graph, monkeypatch):  # noqa: F811
    pid = next(iter(graph.state["programs"]))
    candidate = NS(
        **vars(graph.fence),
        id=uuid4(),
        epoch_id=graph.intent.epoch_id,
        upstream_program_id=UUID(pid),
        ordinal=0,
        parent_candidate_id=None,
        candidate_canonical=graph.state["programs"][pid]["code"],
        program_version_id=uuid4(),
    )
    measured = StructuredMeasurement(
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
    feedback = {"structured_feedback": service.measurement_feedback(measured)}
    graph.state["programs"][pid]["artifacts_json"] = canonical(feedback).decode()
    cursor = graph.rows[0]
    cursor.state_canonical = canonical(graph.state).decode()
    cursor.state_sha256 = state_sha256(graph.state)
    view, _ = selection(graph)
    view["snapshot"]["artifacts"] = {pid: feedback}
    after = deepcopy(graph.state)
    length = float(len(candidate.candidate_canonical))
    after["metadata"]["feature_stats"] = {
        "complexity": {"values": [length, length], "min": length, "max": length}
    }
    payload = {
        "iteration": 1,
        "parent_id": pid,
        "inspiration_ids": [pid],
        "snapshot": view["snapshot"],
        "sampling": {
            "schema_version": "mineagent.oe-structured-sampling-transition.v1",
            "before_state_sha256": cursor.state_sha256,
            "after_state": after,
        },
    }
    leader = KernelIdentity(100, 10, "pid:[42]", "0::/fixed", 1, (0,) * 4, (0,) * 4)
    child = KernelIdentity(
        101, 11, "pid:[42]", "0::/fixed", 100, (65533,) * 4, (65533,) * 4
    )
    peer = NS(binding=ControllerBinding("a" * 64, "b" * 64, leader, child))
    context = NS(config=NS(search=NS(domain=graph.domain, maximum_mutation_slots=2)))
    state = NS(
        cursor=cursor,
        prior=None,
        result=NS(candidate_id=candidate.id, measurement=measured),
        held="reserved",
    )
    added, calls = [], []

    class Session:
        def scalar(self, statement):
            model = statement.column_descriptions[0]["entity"]
            return (
                state.prior
                if model is service.OEStructuredSamplingIntent
                else state.cursor
            )

        def scalars(self, statement):
            return [candidate]

        def add(self, row):
            added.append(row)

        def flush(self):
            calls.append("flush")

    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: calls.append("peer") or context,
    )

    def read(*a, **kw):
        assert kw == {"peer": peer, "current_control_id": cursor.id}
        calls.append("actual_result")
        return state.result

    monkeypatch.setattr(service, "read_sampling_engine_outcome", read)

    def reserve(*a, **kw):
        assert kw["purpose"] == "engine_mutation" and kw["amounts"].mutation_slots == 1
        calls.append("reserve")
        return NS(id=uuid4(), state=state.held)

    monkeypatch.setattr(service, "reserve_budget", reserve)

    def call():
        return service.register_sampling(
            Session(),
            graph.fence,
            candidate.epoch_id,
            canonical(payload),
            NS(),
            peer=peer,
            ticket_key=b"k" * 32,
        )

    return NS(
        call=call,
        state=state,
        payload=payload,
        cursor=cursor,
        added=added,
        calls=calls,
        peer=peer,
    )


def test_sampling_commit_represents_partial_before_placement_and_rendering(scenario):
    row, token = scenario.call()
    assert row.ordinal == 1 and len(token) == 64
    assert len(scenario.added) == 2 and scenario.added[0].phase == "sampling"
    assert not hasattr(row, "rendered_canonical") and not hasattr(
        row, "worker_identity_canonical"
    )
    assert (
        scenario.calls.index("actual_result")
        < scenario.calls.index("reserve")
        < scenario.calls.index("flush")
    )
    scenario.state.prior = row
    scenario.state.cursor = scenario.added[0]
    assert scenario.call() == (row, token)
    assert scenario.calls.count("reserve") == 1


@pytest.mark.parametrize(
    "change",
    [
        "score",
        "artifact",
        "state_hash",
        "cursor",
        "worker",
        "sampling_rewrite",
        "settled_hold",
    ],
)
def test_sampling_rejects_fake_prompt_population_before_persistence(scenario, change):
    if change == "score":
        scenario.state.result = NS(
            candidate_id=scenario.state.result.candidate_id,
            measurement=scenario.state.result.measurement.model_copy(
                update={"combined_score": 6.0}
            ),
        )
    elif change == "artifact":
        scenario.payload["snapshot"]["artifacts"] = {}
    elif change == "state_hash":
        scenario.payload["sampling"]["before_state_sha256"] = "0" * 64
    elif change == "cursor":
        scenario.cursor.phase = "sampling"
    elif change == "worker":
        scenario.peer.binding = object()
    elif change == "sampling_rewrite":
        scenario.payload["sampling"]["after_state"]["metadata"]["last_iteration"] = 1
    elif change == "settled_hold":
        scenario.state.held = "settled"
    with pytest.raises((ProcessError, ValueError)):
        scenario.call()
    assert not scenario.added
