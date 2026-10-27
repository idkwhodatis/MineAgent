"""Request boundary doubles; exact template itself has installed upstream parity probes."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_provider_prompt_guard as module


@pytest.mark.parametrize("seeded", [False, True])
def test_actual_frozen_parent_is_reread_before_exact_first_request(monkeypatch, seeded):
    fence = SimpleNamespace(owner_id=uuid4(), run_id=uuid4())
    row, seed, config = object(), object(), object()
    calls = []
    session = SimpleNamespace(scalar=lambda q: row if seeded else None)
    monkeypatch.setattr(
        module, "require_neutral_transaction", lambda *a, **k: calls.append(k)
    )

    def read(s, o, r, settings):
        assert s is session and o == fence.owner_id and r is row
        calls.append("actual-parent-bytes")
        return seed

    monkeypatch.setattr(module, "reread_saved_seed", read)

    def prompt(c, actual_seed):
        assert c is config and actual_seed is (seed if seeded else None)
        return "frozen system", "frozen task"

    monkeypatch.setattr(module, "first_provider_messages", prompt)
    request = SimpleNamespace(
        kind="chat",
        messages=(
            SimpleNamespace(role="system", content="frozen system"),
            SimpleNamespace(role="user", content="frozen task"),
        ),
    )
    module.require_first_provider_prompt(
        session,
        fence,
        SimpleNamespace(phase="generation", ordinal=0),
        request,
        config,
        object(),
    )
    assert ("actual-parent-bytes" in calls) is seeded
    assert calls[0] == {"resource_scope": "factor_numeric"}
    request.messages[0].content = "hot edited steer or omitted guidance"
    with pytest.raises(ValueError, match="exact frozen"):
        module.require_first_provider_prompt(
            session,
            fence,
            SimpleNamespace(phase="generation", ordinal=0),
            request,
            config,
            object(),
        )
