"""Partial sampling→exact measured worker row; DB/kernel doubles explicitly."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_placement_services as service
from packages.factors.contracts import canonical_bytes
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_worker_observation import case  # noqa: F401


@pytest.fixture
def graph(case, monkeypatch):  # noqa: F811
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    sample = NS(
        **scope,
        id=case.placement.intent_id,
        epoch_id=case.placement.epoch_id,
        sampling_state_id=uuid4(),
        ordinal=1,
        worker_ticket_sha256="c" * 64,
    )
    cursor = NS(
        **scope, id=sample.sampling_state_id, phase="sampling", sequence=2, frontier=0
    )
    state = NS(saved=None, cursor=cursor)
    events = []

    class Session:
        def get(self, cls, key):
            assert cls is service.OEStructuredSamplingIntent and key == sample.id
            return sample

        def scalar(self, statement):
            if (
                statement.column_descriptions[0]["entity"]
                is service.OEStructuredControlState
            ):
                return state.cursor
            return state.saved

        def add(self, row):
            state.saved = row

        def flush(self):
            events.append("flush")

    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: events.append("guard") or "context",
    )
    peer = NS(binding=case.binding, _context=case.context)

    def call():
        return service.record_placement(
            Session(),
            NS(**scope),
            sample.epoch_id,
            canonical_bytes(case.placement),
            NS(),
            peer=peer,
        )

    return NS(call=call, state=state, sample=sample, peer=peer, events=events)


def test_actual_observation_precedes_row_and_repeats_before_commit(graph, case):  # noqa: F811
    row = graph.call()
    assert row.sampling_intent_id == graph.sample.id
    assert case.observe.call_count == 2
    assert graph.events == ["guard", "flush", "guard"]
    assert graph.call() is row
    assert graph.events.count("flush") == 1


@pytest.mark.parametrize(
    "change", ["wrong_epoch", "stale", "advanced", "wrong_phase", "wrong_identity"]
)
def test_no_first_arrival_or_stale_placement_authority(graph, case, change):  # noqa: F811
    if change == "wrong_epoch":
        graph.sample.epoch_id = uuid4()
    elif change == "stale":
        graph.state.cursor.id = uuid4()
    elif change == "advanced":
        graph.state.cursor.sequence = 4
    elif change == "wrong_phase":
        graph.state.cursor.phase = "applied"
    elif change == "wrong_identity":
        case.statuses[102] = (101, (102, 99))
    with pytest.raises((ProcessError, ValueError)):
        graph.call()
    assert graph.state.saved is None
