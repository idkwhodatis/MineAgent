"""Seed bytes and idempotent declaration; no actual process/PG authority claim."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_candidate_registration as service
from backend.app.oe_structured_models import OEStructuredCandidate
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_candidate_authority import setup


def arranged(monkeypatch, tmp_path):
    c = setup(monkeypatch, tmp_path)
    original_get = c.session.get
    state = NS(row=None)
    calls = []

    def get(cls, key):
        if (
            cls is OEStructuredCandidate
            and state.row is not None
            and key == state.row.id
        ):
            return state.row
        return original_get(cls, key)

    c.session.get = get
    c.session.scalar = lambda stmt: state.row
    c.session.add = lambda row: setattr(state, "row", row)
    c.session.flush = lambda: calls.append("flush")
    monkeypatch.setattr(
        service, "require_controller_context", lambda *a, **kw: c.context
    )
    execution_id = uuid4()
    monkeypatch.setattr(
        service,
        "prepare_execution",
        lambda *a, **kw: calls.append("prepare") or execution_id,
    )
    upstream_id = uuid4()

    def call(raw=None, pid=None):
        return service.register_candidate(
            c.session,
            c.fence,
            c.candidate.epoch_id,
            upstream_id if pid is None else pid,
            c.candidate.candidate_canonical.encode() if raw is None else raw,
            c.settings,
            peer=c.peer,
        )

    return NS(call=call, state=state, calls=calls, c=c, execution_id=execution_id)


def test_seed_is_original_user_version_and_replay_does_not_add_another_candidate(
    monkeypatch, tmp_path
):
    test = arranged(monkeypatch, tmp_path)
    row, eid = test.call()
    assert eid == test.execution_id and row.origin == "authenticated_user_seed"
    assert row.program_version_id == test.c.candidate.program_version_id
    assert row.provider_exchange_id is None and row.parent_candidate_id is None
    assert test.call()[0] is row
    assert test.calls.count("flush") == 1


def test_seed_cannot_be_replaced_by_caller_program_or_different_native_id(
    monkeypatch, tmp_path
):
    test = arranged(monkeypatch, tmp_path)
    with pytest.raises(ProcessError):
        test.call(b"{}")
    assert test.state.row is None and not test.calls
    row, _ = test.call()
    with pytest.raises(ProcessError):
        test.call(pid=uuid4())
    assert test.state.row is row and test.calls.count("flush") == 1
