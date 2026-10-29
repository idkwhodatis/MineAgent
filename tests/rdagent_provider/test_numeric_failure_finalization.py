"""Finalization DTO/session doubles; real SQL/physical gates remain separate."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID

import pytest

from backend.app import rdagent_numeric_failure_finalization as module
from runtimes.rdagent_operational.rpc import RPCError
from tests.rdagent_provider.test_numeric_failure_contracts import frame


@pytest.fixture
def graph(monkeypatch):
    failure = frame()
    fence = NS(
        owner_id=UUID(int=20),
        run_id=UUID(int=21),
        attempt_id=UUID(int=22),
        generation=1,
    )
    observed = NS(
        frame=failure,
        source=NS(start_id=UUID(int=23), source_sha256="c" * 64),
        evaluation=NS(input_sha256="d" * 64),
        cutoff=NS(id=failure.cutoff_execution_id),
        controller_launch=NS(id=UUID(int=24)),
    )
    state = NS(published=[], added=[], reads=0, existing=None)

    class Session:
        def scalar(self, _):
            return state.existing

        def add(self, row):
            state.added.append(row)

        def flush(self):
            pass

    @contextmanager
    def transaction(*_):
        yield Session()

    monkeypatch.setattr(module, "snapshot_transaction", transaction)

    def read(*a, **kw):
        state.reads += 1
        return observed

    monkeypatch.setattr(module, "read_candidate_failure", read)

    def phase(session, fence, observed, phase, settings):
        return NS(id=UUID(int=25)), NS(
            content="actual critique"
            if phase == "critique"
            else '{"final_decision":false}'
        )

    monkeypatch.setattr(module, "_closed_phase", phase)

    def publish(factory, fence, settings, kind, raw, key):
        state.published.append(raw)
        return UUID(int=26)

    monkeypatch.setattr(module, "_publish", publish)
    monkeypatch.setattr(module, "read_research_object", lambda *a: state.published[-1])
    feedback = {
        "execution": failure.feedback_text,
        "return_checking": None,
        "code": "actual critique",
        "final_decision": False,
    }
    state.feedback = feedback
    state.call = lambda: module.retain_numeric_failure_feedback(
        None,
        fence,
        None,
        start_id=observed.source.start_id,
        source_id=failure.source_id,
        evaluation_id=failure.evaluation_id,
        upstream_feedback=feedback,
        settings=None,
    )
    return state


def test_actual_feedback_publication_is_rechecked_before_no_snapshot_result(graph):
    assert graph.call() == UUID(int=26)
    assert graph.reads == 2 and len(graph.published) == 1
    result, membership = graph.added
    assert result.status == "execution_failed" and result.snapshot_id is None
    assert membership.evaluation_outcome_id == result.id
    assert membership.final_decision_call_id == UUID(int=25)
    assert membership.rejection_receipt_id == frame().rejection_receipt_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution", "invented"),
        ("code", "invented critique"),
        ("final_decision", True),
        ("return_checking", "invented IC"),
    ],
)
def test_detached_caller_feedback_is_not_published(graph, field, value):
    graph.feedback[field] = value
    with pytest.raises((ValueError, RPCError)):
        graph.call()
    assert not graph.published and not graph.added
