"""Role-specific scheduling predicates preserve original heartbeat semantics."""

import ast
import inspect

import pytest

from backend.app import services
from tests.sql_probes.rd_admission_expiry_observation import HEARTBEAT_EXPIRY_QUERY
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected
from tests.sql_probes.test_rd_admission_expiry_observation_unit import setup_observer


def test_original_heartbeat_terminal_statuses_are_exactly_pinned():
    tree = ast.parse(inspect.getsource(services.fenced_attempt))
    source = ast.unparse(tree)
    assert "run.status in TERMINAL" in source
    assert "attempt.status" not in source
    assert services.TERMINAL == {
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "paused",
    }
    assert (
        "r.status NOT IN ('succeeded','failed','cancelled','interrupted','paused')"
        in HEARTBEAT_EXPIRY_QUERY
    )
    assert "a.status" not in HEARTBEAT_EXPIRY_QUERY
    assert "a.finished_at IS NULL" in HEARTBEAT_EXPIRY_QUERY


def test_heartbeat_ticket_cannot_be_used_as_neutral_ticket():
    observer, factory, fence, state = setup_observer()
    ticket = observer.observe(factory, fence, role="heartbeat", clock=lambda: 10)
    assert state["sql"][0][0] == HEARTBEAT_EXPIRY_QUERY
    assert ticket.remaining(factory, fence, role="heartbeat", clock=lambda: 11) == 9
    with pytest.raises(AdmissionRejected, match="role_mismatch"):
        ticket.remaining(factory, fence, clock=lambda: 11)
    neutral = observer.observe(factory, fence, clock=lambda: 10)
    with pytest.raises(AdmissionRejected, match="role_mismatch"):
        neutral.remaining(factory, fence, role="heartbeat", clock=lambda: 11)


def test_invalid_role_rejected_before_query():
    observer, factory, fence, state = setup_observer()
    for role in (None, "cleanup", "", 1):
        with pytest.raises(AdmissionRejected, match="invalid_role"):
            observer.observe(factory, fence, role=role)
    assert state["sql"] == []
