"""Production broker orchestration with explicit DB/process doubles, not PG proof."""

import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app import rdagent_provider_broker as module
from backend.app import rdagent_workflow_coordination as coord
from packages.rdagent_provider.codec import canonical_bytes
from packages.rdagent_provider.contracts import RDModelBinding, RDProviderChatRequest
from packages.rdagent_provider.process_receipts import parse_receipt, validate_chain
from packages.rdagent_provider.transport import (
    ProcessIdentity,
    ProcessStop,
    ProviderFailure,
)
from packages.research_runtime.fences import AttemptFence


@pytest.fixture
def broker(monkeypatch, tmp_path):
    real_settle = module._settle
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
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    config = SimpleNamespace(model_binding=binding)
    session = SimpleNamespace(scalar=lambda query: None)
    state = SimpleNamespace(
        active=False,
        receipts={},
        calls=0,
        outcomes=[],
        files={},
        published=[],
        before_stop=None,
        after_stop=None,
        unknown_guard=None,
        fail=None,
        auth_count=0,
        fail_auth=None,
    )
    operator = SimpleNamespace(
        connection=SimpleNamespace(
            secret_env="RD_TEST_SECRET", base_url="http://127.0.0.1:1/v1"
        )
    )
    monkeypatch.setenv("RD_TEST_SECRET", "synthetic-no-credential")

    @contextmanager
    def transaction(factory, fence, **kwargs):
        assert not state.active
        with coord.neutral(factory, fence):
            state.active = True
            try:
                yield session
            finally:
                state.active = False

    def publish(factory, fence, settings, kind, raw, key):
        assert not state.active
        identifier = uuid4()
        path = tmp_path / str(identifier)
        with coord.neutral(factory, fence):
            path.write_bytes(raw)
            state.files[identifier] = path
            state.published.append(kind)
        return identifier

    def bind(session, fence, *, transport_id, object_id, sha256, settings):
        assert state.active
        doc = parse_receipt(state.files[object_id].read_bytes())
        if state.fail == doc.kind:
            raise OSError("uncertain receipt commit")
        state.receipts[doc.kind] = doc

    def settle(
        session,
        fence,
        transport_id,
        settings,
        *,
        response_id=None,
        response=None,
        expected_chain=None,
    ):
        assert state.active
        chain = tuple(state.receipts[k] for k in ("intent", "started", "stopped"))
        validate_chain(*chain, completed=response is not None)
        if expected_chain is not None:
            assert response_id is response is None and type(expected_chain) is tuple
            if state.unknown_guard is not None:
                state.unknown_guard(chain, expected_chain)
            assert chain == expected_chain
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
        if state.fail == "identity_drift":
            identity = replace(identity, start_ticks=identity.start_ticks + 1)
        if state.before_stop is not None:
            state.before_stop()
        stop = ProcessStop(
            identity=identity, observed_monotonic_ns=module.time.monotonic_ns()
        )
        identity_boundary("stopped", stop)
        if state.after_stop is not None:
            state.after_stop()
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

    def authorize(peer, actual_session, actual_fence):
        assert state.active and actual_session is session and actual_fence is fence
        assert peer is args["controller_peer"]
        state.auth_count += 1
        if state.fail_auth == state.auth_count:
            raise ValueError("controller changed")

    monkeypatch.setattr(module, "_authorize", authorize)
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
        "controller_peer": object(),
        "original_deadline": time.monotonic() + 10,
    }
    return SimpleNamespace(
        state=state,
        args=args,
        fence=fence,
        factory=sessionmaker(),
        binding=binding,
        real_settle=real_settle,
    )


def test_started_is_retained_before_request_and_stopped_before_success(broker):
    _, raw = module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert b"actual provider text" in raw
    assert broker.state.calls == 1 and broker.state.outcomes == ["completed"]
    assert broker.state.auth_count == 4
    assert set(broker.state.receipts) == {"intent", "started", "stopped"}


@pytest.mark.parametrize(
    "failure,calls,outcomes",
    [
        ("started", 0, []),
        ("stopped", 1, []),
        ("missing_stop", 1, []),
        ("identity_drift", 1, []),
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


@pytest.mark.parametrize(
    "failure", [None, "transport", "usage", "stale", "drift", "elapsed"]
)
def test_positive_stop_is_durable_but_never_publishes_late_response(
    broker, monkeypatch, failure
):
    now = time.monotonic()
    cutoff = now + 1
    clock = [now]
    monkeypatch.setattr(coord, "monotonic", lambda: clock[0])
    monkeypatch.setattr(coord, "monotonic_ns", lambda: int(clock[0] * 1e9))
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0], monotonic_ns=lambda: int(clock[0] * 1e9)
        ),
    )
    broker.args["original_deadline"] = cutoff
    broker.state.before_stop = lambda: clock.__setitem__(0, cutoff + 0.01)
    broker.state.fail = failure
    if failure == "elapsed":
        broker.state.after_stop = lambda: clock.__setitem__(
            0, cutoff + coord.OWNER_WAIT_SECONDS + 0.1
        )
    elif failure in {"stale", "drift"}:

        def refuse_unknown(actual, expected):
            assert actual == expected
            if failure == "stale":
                from backend.app.services import FenceLost

                raise FenceLost()
            raise ValueError("Retained SDK chain differs")

        broker.state.unknown_guard = refuse_unknown
    with (
        coord.registered(broker.factory, broker.fence),
        coord.controller_deadline(broker.factory, broker.fence, cutoff),
        pytest.raises(ValueError if failure == "usage" else ProviderFailure) as caught,
    ):
        module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    if failure != "usage":
        assert str(caught.value) == (
            "provider_error"
            if failure == "transport"
            else "original_controller_deadline"
        )
    assert set(broker.state.receipts) == {"intent", "started", "stopped"}
    expected = [] if failure in {"stale", "drift", "elapsed"} else ["unknown"]
    assert broker.state.calls == 1 and broker.state.outcomes == expected
    assert "model_response" not in broker.state.published


def test_early_stop_does_not_shorten_original_multi_transaction_budget(
    broker, monkeypatch
):
    clock = [100.0]
    original_cutoff = 120.0
    monkeypatch.setattr(coord, "monotonic", lambda: clock[0])
    monkeypatch.setattr(coord, "monotonic_ns", lambda: int(clock[0] * 1e9))
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0], monotonic_ns=lambda: int(clock[0] * 1e9)
        ),
    )
    broker.args["original_deadline"] = original_cutoff
    publish = module._publish

    def publish_with_stopped_receipt_cost(factory, fence, settings, kind, raw, key):
        if kind != module.OBJECT_KIND or parse_receipt(raw).kind != "stopped":
            return publish(factory, fence, settings, kind, raw, key)
        # Publication enters reserve/preflight/finalize separately before its
        # caller enters bind. Each can be short while their total exceeds 5 s.
        for _ in range(2):
            with coord.neutral(factory, fence):
                clock[0] += 2
        identity = publish(factory, fence, settings, kind, raw, key)
        clock[0] += 2
        return identity

    monkeypatch.setattr(module, "_publish", publish_with_stopped_receipt_cost)
    with (
        coord.registered(broker.factory, broker.fence),
        coord.controller_deadline(broker.factory, broker.fence, original_cutoff),
    ):
        _, raw = module.submit_provider_request(
            broker.factory, broker.fence, **broker.args
        )
    assert b"actual provider text" in raw
    assert clock[0] > 100 + coord.OWNER_WAIT_SECONDS
    assert broker.state.outcomes == ["completed"]


@pytest.mark.parametrize("case", ["exact", "drift", "response"])
def test_actual_settlement_requires_exact_retained_stop_before_full_charge(
    broker, monkeypatch, case
):
    from unittest.mock import Mock

    module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    receipts = broker.state.receipts
    expected = tuple(receipts[k] for k in ("intent", "started", "stopped"))
    transport_id = receipts["intent"].transport_id
    protocol = broker.args["protocol"]
    request = module.parse_request(protocol, broker.args["request_bytes"])
    charge = module.request_reservation(request, broker.binding)
    transport = SimpleNamespace(
        id=transport_id,
        state="submitted",
        operation_id=uuid4(),
        reservation_id=uuid4(),
    )
    operation = SimpleNamespace(
        state="open", protocol=protocol, request=request.model_dump(mode="json")
    )
    reservation = SimpleNamespace(amounts=charge.model_dump())
    scope_row = Mock(side_effect=[transport, operation, reservation])
    read_chain = Mock(return_value=dict(receipts))
    settle_budget = Mock(return_value=SimpleNamespace(charge=charge.model_dump()))
    session = Mock()
    monkeypatch.setattr(module, "_scope_row", scope_row)
    monkeypatch.setattr(module, "read_chain", read_chain)
    monkeypatch.setattr(module, "settle_budget", settle_budget)
    kwargs = {"expected_chain": expected}
    if case == "drift":
        stopped = receipts["stopped"]
        read_chain.return_value["stopped"] = stopped.model_copy(
            update={"observed_monotonic_ns": stopped.observed_monotonic_ns + 1}
        )
    elif case == "response":
        kwargs["response_id"] = uuid4()

    if case == "exact":
        outcome = broker.real_settle(
            session, broker.fence, transport_id, broker.args["settings"], **kwargs
        )
        assert outcome.outcome == transport.state == operation.state == "unknown"
        assert outcome.response_object_id is None
        settle_budget.assert_called_once_with(
            session,
            broker.fence,
            transport.reservation_id,
            charge=charge,
            outcome="conservative_unknown",
        )
        session.add.assert_called_once_with(outcome)
    else:
        message = "chain differs" if case == "drift" else "containment-only"
        with pytest.raises(ValueError, match=message):
            broker.real_settle(
                session, broker.fence, transport_id, broker.args["settings"], **kwargs
            )
        assert transport.state == "submitted" and operation.state == "open"
        settle_budget.assert_not_called()
        session.add.assert_not_called()
    if case != "response":
        read_chain.assert_called_once_with(
            session,
            broker.fence,
            transport_id,
            broker.args["settings"],
            completed=False,
        )
    else:
        read_chain.assert_not_called()


def test_expired_original_controller_deadline_never_admits_sdk(broker):
    broker.args["original_deadline"] = time.monotonic() - 1
    with pytest.raises(ProviderFailure, match="original_controller_deadline"):
        module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert not broker.state.receipts and broker.state.calls == 0


@pytest.mark.parametrize("boundary", [1, 2, 3, 4])
def test_every_original_sdk_boundary_reauthorizes_before_private_handoff(
    broker, boundary
):
    broker.state.fail_auth = boundary
    with pytest.raises(ValueError, match="controller changed"):
        module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert broker.state.auth_count == boundary
    assert broker.state.calls == 0 and "completed" not in broker.state.outcomes


def test_provider_handler_passes_same_peer_to_fixed_broker_guard(monkeypatch):
    from backend.app import rdagent_operational_handler as base_handler
    from backend.app import rdagent_provider_handler as handler_module

    authorized = []

    @contextmanager
    def transaction(*args, **kwargs):
        yield object()

    monkeypatch.setattr(base_handler, "neutral_transaction", transaction)
    monkeypatch.setattr(
        base_handler,
        "require_rd_controller",
        lambda session, fence, peer: authorized.append(peer),
    )
    peer = object()
    handler = handler_module.ProviderHandler(object(), object(), object())
    handler.original_deadline = time.monotonic() + 1
    seen = []

    def submit(*args, **kwargs):
        seen.append(kwargs)
        return uuid4(), b"{}"

    monkeypatch.setattr(handler_module, "submit_provider_request", submit)
    handler(
        peer,
        "submit_chat",
        {
            "exchange_id": str(uuid4()),
            "idempotency_key": str(uuid4()),
            "relation": "initial",
            "protocol": "mineagent.rdagent-provider-chat.v1",
            "request_utf8": "{}",
        },
    )
    assert authorized == [peer]
    assert seen[0]["controller_peer"] is peer
    assert "revalidate_controller" not in seen[0]


def test_embedding_primitive_reaches_completed_broker_with_absent_output(
    broker, monkeypatch
):
    from packages.rdagent_provider.contracts import RDProviderEmbeddingRequest
    from tests.rdagent_provider.test_embedding_accounting import response_from_sdk

    binding = broker.binding
    request = RDProviderEmbeddingRequest(
        model_profile_id=binding.profile_id,
        model_binding_sha256=binding.sha256,
        model=binding.embedding_model,
        dimensions=binding.embedding_dimensions,
        inputs=("actual final feedback",),
    )
    original = module.execute

    def sdk(actual, **kwargs):
        assert actual == request
        # Keep the fixture's physical callback ordering; response validation and
        # accounting use real production code, DB/process authority are doubles.
        original(actual, **kwargs)
        response = response_from_sdk(
            actual,
            {
                "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
                "usage": {"prompt_tokens": 100, "total_tokens": 100},
            },
        )
        return SimpleNamespace(vectors=response.vectors, **response.usage.model_dump())

    monkeypatch.setattr(module, "execute", sdk)
    args = dict(
        broker.args,
        protocol=request.schema_version,
        request_bytes=canonical_bytes(request.schema_version, request),
    )
    _, raw = module.submit_provider_request(broker.factory, broker.fence, **args)
    import json

    usage = json.loads(raw)["usage"]
    assert usage["output_tokens"] is None
    assert usage["input_tokens"] == usage["total_tokens"] == 100
    assert usage["billing_status"] == "unknown"
    assert broker.state.calls == 1 and broker.state.outcomes == ["completed"]
    assert set(broker.state.receipts) == {"intent", "started", "stopped"}
