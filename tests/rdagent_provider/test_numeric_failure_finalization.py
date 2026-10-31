"""Finalization DTO/session doubles; real SQL/physical gates remain separate."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID

import pytest

from backend.app import rdagent_numeric_failure_finalization as module
from runtimes.rdagent_operational.rpc import RPCError
from tests.rdagent_provider.test_numeric_failure_contracts import frame

REAL_CLOSED_PHASE = module._closed_phase


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
    state.observed = observed
    state.fence = fence
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


@pytest.mark.parametrize("break_link", [None, "critique_input", "launch", "members"])
def test_real_phase_queries_and_typed_response_reach_exact_finalization(
    graph, monkeypatch, break_link
):
    """ORM/query and typed codec proof; DB/transport/physical reads remain doubles."""
    from sqlalchemy.dialects.postgresql import dialect

    from backend.app import rdagent_services
    from backend.app.rdagent_models import (
        FactorGenerationModelOperation,
        FactorUpstreamCall,
        FactorUpstreamCallOutcome,
    )
    from backend.app.research_broker_models import ResearchModelOperation
    from packages.rdagent_provider.contracts import RDProviderChatResponse

    scope = vars(graph.fence)
    calls = {
        phase: FactorUpstreamCall(
            id=UUID(int=30 + index),
            **scope,
            start_id=graph.observed.source.start_id,
            context_id=UUID(int=50),
            phase=phase,
            kind="chat",
            ordinal=index,
        )
        for index, phase in enumerate(("critique", "final_decision"))
    }
    member = FactorGenerationModelOperation(operation_id=UUID(int=60))
    operation = ResearchModelOperation(
        id=member.operation_id,
        **scope,
        request={
            "messages": [
                {"role": "user", "content": graph.observed.frame.feedback_text}
            ]
        },
    )
    if break_link == "critique_input":
        operation.request = {"messages": [{"role": "user", "content": "unrelated"}]}
    closed = FactorUpstreamCallOutcome(
        id=UUID(int=61),
        **scope,
        status="completed",
        member_count=1,
        controller_launch_id=graph.observed.controller_launch.id,
    )
    if break_link == "launch":
        closed.controller_launch_id = UUID(int=62)
    active = []

    class Session:
        def scalars(self, query):
            assert query.column_descriptions[0]["entity"] is FactorUpstreamCall
            params = query.compile(dialect=dialect()).params
            assert {
                key.removesuffix("_1"): value
                for key, value in params.items()
                if key.removesuffix("_1") in scope
            } == scope
            phase = params["phase_1"]
            active[:] = [phase]
            return NS(all=lambda: [calls[phase]])

        def scalar(self, query):
            entity = query.column_descriptions[0]["entity"]
            if entity is FactorUpstreamCallOutcome:
                assert (
                    query.compile(dialect=dialect()).params["call_id_1"]
                    == calls[active[0]].id
                )
                return closed
            return graph.existing

        def add(self, row):
            graph.added.append(row)

        def flush(self):
            pass

    @contextmanager
    def transaction(*_):
        yield Session()

    def response(*_):
        content = (
            "actual critique" if active[0] == "critique" else '{"final_decision":false}'
        )
        return None, RDProviderChatResponse(
            content=content,
            finish_reason="stop",
            usage={
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "usage_source": "synthetic_provider",
            },
        )

    monkeypatch.setattr(module, "snapshot_transaction", transaction)
    monkeypatch.setattr(module, "_closed_phase", REAL_CLOSED_PHASE)
    monkeypatch.setattr(
        module, "_call_members", lambda *_: [] if break_link == "members" else [member]
    )
    monkeypatch.setattr(module, "_completed_member_response", response)
    monkeypatch.setattr(rdagent_services, "_scope_row", lambda *_: operation)
    if break_link is not None:
        with pytest.raises(RPCError):
            graph.call()
        assert not graph.published and not graph.added
    else:
        assert graph.call() == UUID(int=26)
        result, membership = graph.added
        assert result.status == "execution_failed" and result.snapshot_id is None
        assert membership.final_decision_call_id == calls["final_decision"].id
        assert membership.evaluation_outcome_id == result.id
        assert graph.reads == 2
