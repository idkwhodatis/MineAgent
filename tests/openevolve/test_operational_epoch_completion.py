"""Terminal barriers with inert DB rows; no actual process/PG acceptance claim."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_epoch_completion as target


def setup(monkeypatch):
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    epoch_id, controller_id = uuid4(), uuid4()
    checkpoint = NS(
        id=uuid4(),
        intent_id=uuid4(),
        inventory_object_id=uuid4(),
        inventory_sha256="a" * 64,
        closure_object_id=uuid4(),
        closure_sha256="b" * 64,
    )
    scope = vars(fence)
    admission = NS(id=controller_id, **scope)
    stop = NS(id=uuid4(), admission_id=controller_id, payload={}, **scope)
    state = NS(status="stopped")
    calls = []

    class Session:
        def __init__(self):
            self.checkpoints = [checkpoint]
            self.latest_intent = NS(id=checkpoint.intent_id, save_ordinal=3, frontier=1)
            self.process_stop = stop
            self.process_state = state

        def scalars(self, query):
            return NS(all=lambda: self.checkpoints)

        def scalar(self, query):
            if (
                query.column_descriptions[0]["entity"]
                is target.m.SearchCheckpointIntent
            ):
                return self.latest_intent
            return self.process_stop

        def get(self, model, identity):
            assert model is target.ResearchProcessState
            return self.process_state

        def flush(self):
            pytest.fail("Incomplete physical evidence reached terminal mutation")

    session = Session()
    monkeypatch.setattr(
        target, "require_neutral_transaction", lambda *a, **kw: calls.append("fence")
    )
    monkeypatch.setattr(
        target, "_locked_epoch", lambda *a: NS(admission_id=controller_id)
    )
    monkeypatch.setattr(
        target,
        "revalidate_current_checkpoint",
        lambda *a: calls.append("retained_checkpoint"),
    )
    monkeypatch.setattr(target, "_rows", lambda *a: [admission])
    args = {
        "epoch_id": epoch_id,
        "frontier": 1,
        "controller_admission_id": controller_id,
        "actual_exit_code": 124,
        "original_deadline_completion": True,
        "settings": NS(storage_root="inert"),
    }
    return session, fence, args, calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("actual_exit_code", 0),
        ("actual_exit_code", True),
        ("original_deadline_completion", False),
        ("frontier", True),
        ("frontier", 0),
        ("frontier", 3),
    ],
)
def test_missing_actual_deadline_completion_cannot_finalize(monkeypatch, field, value):
    session, fence, args, calls = setup(monkeypatch)
    args[field] = value
    with pytest.raises(ValueError, match="protected deadline"):
        target.complete_initial_epoch(session, fence, **args)
    assert calls == ["fence"]


def test_other_controller_cannot_consume_epoch(monkeypatch):
    session, fence, args, calls = setup(monkeypatch)
    args["controller_admission_id"] = uuid4()
    with pytest.raises(ValueError, match="Controller epoch differs"):
        target.complete_initial_epoch(session, fence, **args)
    assert calls == ["fence"]


@pytest.mark.parametrize("copies", [0, 2])
def test_missing_or_duplicate_final_checkpoint_cannot_finalize(monkeypatch, copies):
    session, fence, args, calls = setup(monkeypatch)
    session.checkpoints *= copies
    with pytest.raises(ValueError, match="Exactly one"):
        target.complete_initial_epoch(session, fence, **args)
    assert calls == ["fence"]


def test_empty_process_graph_is_not_positive_stop(monkeypatch):
    session, fence, args, calls = setup(monkeypatch)
    monkeypatch.setattr(target, "_rows", lambda *a: [])
    with pytest.raises(ValueError, match="whole-unit physical closure"):
        target.complete_initial_epoch(session, fence, **args)
    assert calls == ["fence", "retained_checkpoint"]


@pytest.mark.parametrize(
    "fault", ["missing_state", "uncertain", "missing_stop", "wrong_scope"]
)
def test_unresolved_or_substituted_stop_never_reaches_terminal_mutation(
    monkeypatch, fault
):
    session, fence, args, calls = setup(monkeypatch)
    if fault == "missing_state":
        session.process_state = None
    elif fault == "uncertain":
        session.process_state.status = "uncertain"
    elif fault == "missing_stop":
        session.process_stop = None
    else:
        session.process_stop.owner_id = uuid4()
    with pytest.raises(ValueError, match="Unresolved actual process"):
        target.complete_initial_epoch(session, fence, **args)
    assert calls == ["fence", "retained_checkpoint"]


@pytest.mark.parametrize("ordinal,frontier", [(None, 1), (True, 1), (5, 1), (3, 0)])
def test_latest_save_must_be_bounded_final_frontier(monkeypatch, ordinal, frontier):
    session, fence, args, calls = setup(monkeypatch)
    session.latest_intent.save_ordinal = ordinal
    session.latest_intent.frontier = frontier
    with pytest.raises(ValueError, match="Latest actual save"):
        target.complete_initial_epoch(session, fence, **args)
    assert calls == ["fence"]
