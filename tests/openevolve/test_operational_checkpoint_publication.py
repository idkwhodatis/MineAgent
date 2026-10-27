"""Actual complete-graph projection with explicit SQL/session doubles only."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_checkpoint_publication as s
from packages.openevolve_adapter.host_contracts import InitialCheckpointClosure
from packages.openevolve_adapter.observer_transport import canonical


def graph(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    epoch = NS(id=uuid4(), spec_sha256="a" * 64)

    def row(**kw):
        return NS(id=uuid4(), **vars(fence), **kw)

    control = row(sequence=1, kind="seed")
    intent = NS(
        id=uuid4(), frontier=0, control_transition_id=control.id, state_sha256="b" * 64
    )
    data = {model: [] for model in s.TABLES.values()}
    for key in (
        "controller_event_receipt_ids",
        "candidate_ids",
        "objective_receipt_ids",
        "objective_ack_ids",
        "evaluation_ids",
    ):
        data[s.TABLES[key]] = [row()]
    data[s.m.SearchControlTransition] = [control]
    data[s.m.SearchTerminalApplication] = [row(worker_outcome_id=None)]
    data[s.m.SearchEvaluationOutcome] = [
        row(evaluation_id=data[s.m.SearchEvaluation][0].id)
    ]

    class Session:
        def scalars(self, query):
            assert query._limit_clause.value == 65
            model = query.column_descriptions[0]["entity"]
            return NS(all=lambda: data[model])

    monkeypatch.setattr(s, "_latest_control", lambda *a: control)
    return fence, epoch, intent, data, Session(), row


def test_seed_graph_complete_canonical_and_not_resumable(monkeypatch):
    fence, epoch, intent, data, session, _row = graph(monkeypatch)
    closure = s.collect_closure(session, fence, epoch, intent)
    raw = canonical(closure.model_dump(mode="json"))
    assert InitialCheckpointClosure.model_validate_json(raw) == closure
    assert closure.continuation == "not_admitted_initial_epoch_only"
    assert closure.frontier == 0
    assert closure.pending_intent_ids == closure.open_worker_assignment_ids == ()
    for key, model in s.TABLES.items():
        assert set(getattr(closure, key)) == {item.id for item in data[model]}
    assert len(closure.evaluation_outcome_ids) == 1


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_any_foreign_graph_row_rejected(monkeypatch, field):
    fence, epoch, intent, data, session, _row = graph(monkeypatch)
    setattr(
        data[s.m.SearchCandidate][0], field, 2 if field == "generation" else uuid4()
    )
    with pytest.raises(ValueError, match="scope"):
        s.collect_closure(session, fence, epoch, intent)


def test_outcome_scope_rejected(monkeypatch):
    fence, epoch, intent, data, session, _row = graph(monkeypatch)
    data[s.m.SearchEvaluationOutcome][0].owner_id = uuid4()
    with pytest.raises(ValueError, match="outcome bound or scope"):
        s.collect_closure(session, fence, epoch, intent)


@pytest.mark.parametrize("outcome", [False, True])
def test_overbound_query_results_rejected(monkeypatch, outcome):
    fence, epoch, intent, data, session, row = graph(monkeypatch)
    model = s.m.SearchEvaluationOutcome if outcome else s.m.SearchCandidate
    data[model] = [row() for _ in range(65)]
    with pytest.raises(ValueError, match="bound"):
        s.collect_closure(session, fence, epoch, intent)


def test_no_current_control_cannot_publish(monkeypatch):
    fence, epoch, intent, _data, session, _row = graph(monkeypatch)
    monkeypatch.setattr(s, "_latest_control", lambda *a: None)
    with pytest.raises(ValueError, match="cursor"):
        s.collect_closure(session, fence, epoch, intent)


def test_control_changed_during_upload_rejected(monkeypatch):
    fence, epoch, intent, _data, session, _row = graph(monkeypatch)
    intent.control_transition_id = uuid4()
    with pytest.raises(ValueError, match="cursor"):
        s.collect_closure(session, fence, epoch, intent)


def test_missing_required_collection_is_not_complete(monkeypatch):
    fence, epoch, intent, data, session, _row = graph(monkeypatch)
    data[s.m.SearchObjectiveAcknowledgement] = []
    with pytest.raises(ValueError):
        s.collect_closure(session, fence, epoch, intent)


def test_prefetched_intent_not_falsely_applied(monkeypatch):
    fence, epoch, intent, data, session, row = graph(monkeypatch)
    pending = row()
    data[s.m.SearchIterationIntent] = [pending]
    data[s.w.SearchWorkerTicket] = [row()]
    data[s.w.SearchWorkerExpectedIdentity] = [row()]
    assigned = row()
    data[s.w.SearchWorkerAssignment] = [assigned]
    closure = s.collect_closure(session, fence, epoch, intent)
    assert closure.pending_intent_ids == (pending.id,)
    assert closure.open_worker_assignment_ids == (assigned.id,)
