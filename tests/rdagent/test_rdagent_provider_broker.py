"""Production broker orchestration with explicit DB/process doubles, not PG proof."""

import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_provider_broker as module
from packages.rdagent_provider.codec import canonical_bytes
from packages.rdagent_provider.contracts import RDModelBinding, RDProviderChatRequest
from packages.rdagent_provider.process_receipts import parse_receipt, validate_chain
from packages.rdagent_provider.transport import (
    ProcessIdentity,
    ProcessStop,
    ProviderFailure,
)


@pytest.fixture
def broker(monkeypatch, tmp_path):
    binding = RDModelBinding(
        profile_id="local-double",
        profile_sha256="a" * 64,
        chat_model="chat",
        embedding_model="embed",
        embedding_dimensions=2,
        max_output_tokens=128,
        request_deadline_ms=8000,
        synthetic=True,
        pricing={
            "status": "operator_declared",
            "chat_input_microusd_per_million": 1,
            "chat_output_microusd_per_million": 1,
            "embedding_microusd_per_million": 1,
        },
    )
    request = RDProviderChatRequest(
        model_profile_id=binding.profile_id,
        model_binding_sha256=binding.sha256,
        model=binding.chat_model,
        messages=({"role": "user", "content": "Downside dispersion"},),
        max_output_tokens=128,
        response_format=None,
    )
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    config = SimpleNamespace(model_binding=binding)
    session = SimpleNamespace(scalar=lambda query: None)
    state = SimpleNamespace(
        active=False, receipts={}, calls=0, outcomes=[], files={}, fail=None
    )
    operator = SimpleNamespace(
        connection=SimpleNamespace(
            secret_env="RD_TEST_SECRET", base_url="http://127.0.0.1:1/v1"
        )
    )
    monkeypatch.setenv("RD_TEST_SECRET", "synthetic-no-credential")

    @contextmanager
    def transaction(*args, **kwargs):
        assert not state.active
        state.active = True
        try:
            yield session
        finally:
            state.active = False

    def publish(factory, fence, settings, kind, raw, key):
        assert not state.active
        identifier = uuid4()
        path = tmp_path / str(identifier)
        path.write_bytes(raw)
        state.files[identifier] = path
        return identifier

    def bind(session, fence, *, transport_id, object_id, sha256, settings):
        assert state.active
        doc = parse_receipt(state.files[object_id].read_bytes())
        if state.fail == doc.kind:
            raise OSError("uncertain receipt commit")
        state.receipts[doc.kind] = doc

    def settle(
        session, fence, transport_id, settings, *, response_id=None, response=None
    ):
        assert state.active
        validate_chain(
            *(state.receipts[k] for k in ("intent", "started", "stopped")),
            completed=response is not None,
        )
        state.outcomes.append("completed" if response is not None else "unknown")

    def sdk(request, *, identity_boundary, deadline_monotonic, **kwargs):
        assert not state.active
        intent = state.receipts["intent"]
        identity = ProcessIdentity(
            pid=1234,
            start_ticks=5678,
            process_group=1234,
            session_id=1234,
            parent_pid=123,
            boot_id=str(intent.boot_id),
            deadline_monotonic_ns=int(deadline_monotonic * 1e9),
        )
        identity_boundary("started", identity)
        assert "started" in state.receipts
        state.calls += 1  # Simulated private pipe handoff, only after durable callback.
        if state.fail == "missing_stop":
            raise ProviderFailure("group_unconfirmed", contained=False)
        stop = ProcessStop(identity=identity, observed_monotonic_ns=time.monotonic_ns())
        identity_boundary("stopped", stop)
        if state.fail == "transport":
            raise ProviderFailure("provider_error", contained=True)
        return SimpleNamespace(
            content='{"code":"actual provider text"}',
            finish_reason="stop",
            input_tokens=None if state.fail == "usage" else 2,
            output_tokens=3,
            total_tokens=5,
            provider_request_id="local-id",
            usage_source="synthetic_provider",
            billing_status="unknown",
        )

    monkeypatch.setattr(module, "neutral_transaction", transaction)
    monkeypatch.setattr(module, "_authorize", lambda *a: None)
    monkeypatch.setattr(module, "require_research_spec", lambda *a: (None, config))
    monkeypatch.setattr(
        module, "resolve_provider_declaration", lambda *a: (operator, binding)
    )
    monkeypatch.setattr(module, "resolve_provider_runtime", lambda *a: None)
    monkeypatch.setattr(module, "_publish", publish)
    monkeypatch.setattr(module, "bind_receipt", bind)
    monkeypatch.setattr(module, "_settle", settle)
    monkeypatch.setattr(module, "execute", sdk)
    monkeypatch.setattr(module, "mark_submitted", lambda *a: None)
    monkeypatch.setattr(
        module,
        "admit_atomic_request",
        lambda *a, **k: tuple(SimpleNamespace(id=uuid4()) for _ in range(3)),
    )
    args = {
        "call_id": uuid4(),
        "idempotency_key": uuid4(),
        "relation": "initial",
        "protocol": request.schema_version,
        "request_bytes": canonical_bytes(request.schema_version, request),
        "settings": SimpleNamespace(rdagent_provider_python="/usr/bin/python3"),
        "revalidate_controller": object(),
        "original_deadline": time.monotonic() + 10,
    }
    return SimpleNamespace(state=state, args=args, fence=fence, factory=object())


def test_started_is_retained_before_request_and_stopped_before_success(broker):
    _, raw = module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert b"actual provider text" in raw
    assert broker.state.calls == 1 and broker.state.outcomes == ["completed"]
    assert set(broker.state.receipts) == {"intent", "started", "stopped"}


@pytest.mark.parametrize(
    "failure,calls,outcomes",
    [
        ("started", 0, []),
        ("stopped", 1, []),
        ("missing_stop", 1, []),
        ("transport", 1, ["unknown"]),
        ("usage", 1, ["unknown"]),
    ],
)
def test_uncertain_physical_or_usage_evidence_never_returns_success(
    broker, failure, calls, outcomes
):
    broker.state.fail = failure
    with pytest.raises((ValueError, OSError, ProviderFailure)):
        module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert broker.state.calls == calls and broker.state.outcomes == outcomes


def test_expired_original_controller_deadline_never_admits_sdk(broker):
    broker.args["original_deadline"] = time.monotonic() - 1
    with pytest.raises(ProviderFailure, match="original_controller_deadline"):
        module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert not broker.state.receipts and broker.state.calls == 0
