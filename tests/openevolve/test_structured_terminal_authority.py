"""Exact preterminal/replay membership guards with actual state codec and DB doubles."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint

from backend.app import oe_structured_candidate_services as service
from backend.app.oe_structured_models import SCOPE
from backend.app.oe_structured_terminal_models import (
    OEStructuredTerminalApplication,
    OEStructuredWorkerOutcome,
)
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401
from tests.openevolve.test_structured_sampling_authority import case  # noqa: F401


def call(c, cursor):
    return service.require_terminal_candidate(
        c.session,
        c.fence,
        c.candidate.id,
        c.settings,
        peer=c.peer,
        current_control_id=cursor,
    )


def test_no_cursor_seed_only_read(case):  # noqa: F811
    case.latest[:] = [None, None]
    assert call(case, None).candidate_id == case.candidate.id
    assert all(x.get("intent_id") is None for x in case.calls)


def test_sampling_allows_existing_real_before_member(case):  # noqa: F811
    case.control.phase, case.control.sequence = "sampling", 2
    assert call(case, case.control.id).candidate_id == case.candidate.id


def test_terminal_replay_requires_actual_application_and_retained_member(case):  # noqa: F811
    old = case.session.scalar
    application = NS(
        **vars(case.fence), id=uuid4(), epoch_id=case.control.epoch_id, ordinal=0
    )

    def query(statement):
        if (
            statement.column_descriptions[0]["entity"]
            is OEStructuredTerminalApplication
        ):
            return application
        return old(statement)

    case.session.scalar = query
    assert call(case, case.control.id).candidate_id == case.candidate.id


@pytest.mark.parametrize(
    "change", ["cursor", "worker", "hash", "member", "ordinal", "after", "scope"]
)
def test_terminal_guard_rejects_unrelated_or_changed_member(case, change):  # noqa: F811
    case.control.phase, case.control.sequence = "sampling", 2
    if change == "cursor":
        case.latest[0] = NS(id=uuid4())
    elif change == "worker":
        case.peer.binding = object()
    elif change == "hash":
        case.control.state_sha256 = "0" * 64
    elif change == "member":
        case.candidate.upstream_program_id = uuid4()
    elif change == "ordinal":
        case.candidate.ordinal = 2
    elif change == "after":
        case.latest[1] = NS(id=uuid4())
    elif change == "scope":
        case.candidate.owner_id = uuid4()
    with pytest.raises(ProcessError):
        call(case, case.control.id)


def test_seed_cannot_skip_an_existing_cursor(case):  # noqa: F811
    with pytest.raises(ProcessError):
        call(case, None)


def test_model_worker_uses_full_scoped_engine_outcome_identity():
    links = OEStructuredWorkerOutcome.__table__.foreign_key_constraints
    exact = [
        link
        for link in links
        if tuple(link.column_keys)
        == (*SCOPE, "execution_id", "candidate_id", "engine_outcome_id")
    ]
    assert len(exact) == 1
    assert tuple(x.target_fullname for x in exact[0].elements) == tuple(
        f"oe_structured_engine_outcomes.{key}"
        for key in (*SCOPE, "execution_id", "candidate_id", "id")
    )
    checks = " ".join(
        str(x.sqltext)
        for x in OEStructuredWorkerOutcome.__table__.constraints
        if isinstance(x, CheckConstraint)
    )
    assert "status='worker_error'" in checks and "engine_outcome_id IS NULL" in checks
    assert "status='evaluated'" in checks and "engine_outcome_id IS NOT NULL" in checks


def test_model_terminal_seed_has_no_fabricated_worker_outcome():
    checks = " ".join(
        str(x.sqltext)
        for x in OEStructuredTerminalApplication.__table__.constraints
        if isinstance(x, CheckConstraint)
    )
    assert "ordinal=0 AND outcome='seed' AND worker_outcome_id IS NULL" in checks
    assert "ordinal BETWEEN 1 AND 2" in checks


@pytest.mark.parametrize(
    "change", [None, "wrong_child", "wrong_predecessor", "accepted", "error"]
)
def test_rejected_child_replay_is_exact_application_not_arbitrary_nonmember(
    case,  # noqa: F811
    monkeypatch,
    change,
):
    import json

    from backend.app.oe_structured_models import OEStructuredProviderExchange
    from backend.app.oe_structured_operational_models import OEStructuredIterationIntent
    from packages.openevolve_structured.checkpoint import canonical, state_sha256

    c = case
    c.control.phase, c.control.sequence, c.control.frontier = "applied", 3, 1
    c.control.previous_id = uuid4()
    state = json.loads(c.control.state_canonical)
    state["metadata"]["last_iteration"] = 1
    c.control.state_canonical = canonical(state).decode()
    c.control.state_sha256 = state_sha256(state)
    c.candidate.ordinal = 1
    c.candidate.upstream_program_id = uuid4()  # rejected child is NOT in population
    c.candidate.provider_exchange_id = uuid4()
    c.candidate.parent_candidate_id = uuid4()
    worker = NS(
        **vars(c.fence),
        id=uuid4(),
        epoch_id=c.control.epoch_id,
        candidate_id=c.candidate.id,
        status="evaluated",
        iteration_intent_id=uuid4(),
    )
    intent = NS(
        **vars(c.fence),
        id=worker.iteration_intent_id,
        epoch_id=c.control.epoch_id,
        ordinal=1,
        sampling_state_id=c.control.previous_id,
        parent_candidate_id=c.candidate.parent_candidate_id,
    )
    exchange = NS(
        **vars(c.fence),
        id=c.candidate.provider_exchange_id,
        iteration_intent_id=intent.id,
    )
    app = NS(
        **vars(c.fence),
        id=uuid4(),
        epoch_id=c.control.epoch_id,
        ordinal=1,
        outcome="rejected",
        worker_outcome_id=worker.id,
    )
    old_get, old_scalar = c.session.get, c.session.scalar
    rows = {
        (OEStructuredWorkerOutcome, worker.id): worker,
        (OEStructuredIterationIntent, intent.id): intent,
        (OEStructuredProviderExchange, exchange.id): exchange,
    }
    c.session.get = lambda cls, key: rows.get((cls, key)) or old_get(cls, key)
    c.session.scalar = lambda stmt: (
        app
        if stmt.column_descriptions[0]["entity"] is OEStructuredTerminalApplication
        else old_scalar(stmt)
    )
    marker = object()
    calls = []
    monkeypatch.setattr(
        service,
        "_completed_candidate_provenance",
        lambda *a, **kw: calls.append(kw) or marker,
    )
    if change == "wrong_child":
        worker.candidate_id = uuid4()
    elif change == "wrong_predecessor":
        intent.sampling_state_id = uuid4()
    elif change == "accepted":
        app.outcome = "accepted"
    elif change == "error":
        worker.status = "worker_error"
    if change is None:
        assert call(c, c.control.id) is marker
        assert calls[0]["upper_ordinal"] == 2 and calls[0]["current_intent_id"] is None
    else:
        with pytest.raises(ProcessError):
            call(c, c.control.id)
        assert not calls
