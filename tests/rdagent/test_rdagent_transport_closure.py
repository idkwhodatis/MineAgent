from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.app.rdagent_services import _closed_transport_chain
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence


@pytest.mark.parametrize(
    "mode",
    [
        "closed",
        "prior_unknown",
        "gap",
        "pending_budget",
        "missing_outcome",
        "foreign_owner",
        "overcharge",
        "empty",
        "usage_drift",
        "zero_calls",
        "unmeasured",
    ],
)
def test_all_physical_attempts_close_before_upstream_exchange(mode):
    fence = AttemptFence(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        lease_token=UUID(int=4),
    )
    scope = {
        "owner_id": fence.owner_id,
        "run_id": fence.run_id,
        "attempt_id": fence.attempt_id,
        "generation": 1,
    }
    operation = SimpleNamespace(id=UUID(int=10), protocol="mineagent.rdagent-chat.v1")
    chain, reservations, answers = [], {}, []
    for i, state in enumerate(("failed", "completed"), 1):
        row = SimpleNamespace(
            **scope,
            id=UUID(int=10 + i),
            operation_id=operation.id,
            ordinal=i,
            state=state,
            reservation_id=UUID(int=20 + i),
        )
        outcome = SimpleNamespace(
            **scope,
            id=UUID(int=30 + i),
            outcome=state,
            usage=BudgetVector(calls=1, tokens=50).model_dump(),
        )
        reservation = SimpleNamespace(
            **scope,
            id=row.reservation_id,
            purpose="model_transport",
            state="settled",
            amounts=BudgetVector(calls=1, tokens=100).model_dump(),
        )
        settlement = SimpleNamespace(
            **scope,
            charge=BudgetVector(calls=1, tokens=50).model_dump(),
            outcome="measured",
        )
        chain.append(row)
        reservations[row.reservation_id] = reservation
        answers.extend([outcome, settlement])
    if mode == "prior_unknown":
        chain[0].state = "unknown"
    elif mode == "gap":
        chain[1].ordinal = 3
    elif mode == "pending_budget":
        reservations[chain[0].reservation_id].state = "uncertain"
    elif mode == "missing_outcome":
        answers[0] = None
    elif mode == "foreign_owner":
        chain[0].owner_id = UUID(int=99)
    elif mode == "overcharge":
        answers[1].charge = BudgetVector(calls=2).model_dump()
    elif mode == "empty":
        chain = []
    elif mode == "usage_drift":
        answers[0].usage = BudgetVector(calls=1, tokens=40).model_dump()
    elif mode == "zero_calls":
        answers[0].usage = answers[1].charge = BudgetVector(tokens=50).model_dump()
    elif mode == "unmeasured":
        answers[1].outcome = "conservative_unknown"
    sequence = iter(answers)
    session = SimpleNamespace(
        scalars=lambda query: SimpleNamespace(all=lambda: chain),
        scalar=lambda query: next(sequence),
        get=lambda model, identity: reservations[identity],
    )
    if mode == "closed":
        assert _closed_transport_chain(session, fence, operation).id == UUID(int=32)
    else:
        with pytest.raises(DomainError):
            _closed_transport_chain(session, fence, operation)
