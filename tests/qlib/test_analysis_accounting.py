"""Reservation authority doubles; no PostgreSQL claim."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.analysis_process_services import _completed_accounting
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError


@pytest.mark.parametrize(
    "state,outcome,full,valid",
    [
        ("reserved", None, True, True),
        ("uncertain", None, True, True),
        ("settled", "conservative_unknown", True, True),
        ("settled", "failed", True, False),
        ("settled", "conservative_unknown", False, False),
        ("refunded", None, True, False),
        ("reserved", "conservative_unknown", True, False),
    ],
)
def test_only_held_or_full_conservative_charge(state, outcome, full, valid):
    fence = AttemptFence(
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        lease_token=uuid4(),
    )
    scope = {
        name: getattr(fence, name)
        for name in ("owner_id", "run_id", "attempt_id", "generation")
    }
    admission = SimpleNamespace(reservation_id=uuid4(), logical_operation_id=uuid4())
    amounts = {
        "process_slots": 1,
        "evaluator_executions": 1,
        "wall_milliseconds": 60000,
        "storage_bytes": 67174400,
        "calls": 0,
        "tokens": 0,
    }
    reservation = SimpleNamespace(
        **scope,
        purpose="process_unit",
        logical_key="direct-analysis:" + str(admission.logical_operation_id),
        amounts=amounts,
        state=state,
    )
    settlement = (
        None
        if outcome is None
        else SimpleNamespace(
            **scope,
            outcome=outcome,
            charge=amounts if full else {**amounts, "process_slots": 0},
        )
    )
    session = SimpleNamespace(get=lambda *_: reservation, scalar=lambda *_: settlement)
    if valid:
        _completed_accounting(session, fence, admission)
    else:
        with pytest.raises(ProcessError):
            _completed_accounting(session, fence, admission)
