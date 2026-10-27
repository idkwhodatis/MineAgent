"""Run-wide provider call cap; no model transport or DB execution claims."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.rdagent_provider_quota import require_provider_call_capacity
from backend.app.services import DomainError


@pytest.mark.parametrize("count", [0, 31, 32, 33, None, True])
def test_call_bound_counts_all_attempts_and_requires_fence(monkeypatch, count):
    from backend.app import research_lifecycle

    fence = SimpleNamespace(owner_id=uuid4(), run_id=uuid4())
    scopes = []
    monkeypatch.setattr(
        research_lifecycle,
        "require_neutral_transaction",
        lambda s, f, **kw: scopes.append(kw["resource_scope"]),
    )
    statements = []

    def scalar(query):
        statements.append(str(query))
        return count

    session = SimpleNamespace(scalar=scalar)
    if type(count) is int and 0 <= count < 32:
        require_provider_call_capacity(session, fence)
    else:
        with pytest.raises(DomainError) as exc:
            require_provider_call_capacity(session, fence)
        assert exc.value.code == "rd_provider_call_limit"
    assert scopes == ["factor_numeric"]
    assert "attempt_id" not in statements[0] and "run_id" in statements[0]
