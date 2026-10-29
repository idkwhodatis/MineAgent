"""Real request codec with explicit boundary doubles; no kernel/PG claim."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_socket_services as service
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_contracts import OEChatResponse
from packages.openevolve_structured.provider_response import build_request
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_contracts import declared


def arranged(monkeypatch):
    _, binding, domain, _, parent, rendered, context, _ = declared()
    request = build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=parent,
        rendered=rendered,
    )
    response = OEChatResponse(
        request_sha256=request.sha256,
        content=parent.decode(),
        finish_reason="stop",
        usage_source="provider",
    )
    key = b"k" * 32
    admission = uuid4()
    peer, channel, running = NS(admission=NS(id=admission)), object(), object()
    bound = NS(
        intent=NS(id=context.iteration_intent_id),
        context=context,
        request=request,
        config=NS(),
    )
    exchange = NS(id=uuid4(), iteration_intent_id=bound.intent.id)
    state = NS(active=False, locked=False, changed=False, fail_commit=False)
    calls = []

    @contextmanager
    def guard(*a):
        assert not state.locked
        state.locked = True
        try:
            yield
        finally:
            state.locked = False

    @contextmanager
    def txn(*a, **kw):
        assert state.locked and not state.active
        assert kw["resource_scope"] == "factor_numeric"
        state.active = True
        try:
            yield NS(get=lambda *a: exchange)
            if state.fail_commit:
                raise ProcessError("commit_failed")
            calls.append("commit")
        finally:
            state.active = False

    def authenticate(*a, **kw):
        assert state.active and state.locked and a[4] is channel
        assert kw["running"] is running
        assert kw["sampling_id"] == context.iteration_intent_id
        calls.append("authenticate")
        return peer

    def require(*a, **kw):
        assert state.active and state.locked and kw["peer"] is peer
        if state.changed:
            raise ProcessError("changed")
        return bound

    def submit(*a, **kw):
        assert not state.active and not state.locked and kw["peer"] is peer
        assert a[2] == bound.intent.id and a[3] == canonical_bytes(request)
        calls.append("sdk")
        return exchange.id, canonical_bytes(response)

    monkeypatch.setattr(service, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(service, "native_storage_guard", guard)
    monkeypatch.setattr(service, "neutral_transaction", txn)
    monkeypatch.setattr(service, "authenticate_channel", authenticate)
    monkeypatch.setattr(service, "require_provider_operation", require)
    monkeypatch.setattr(service, "submit_provider_chat", submit)
    monkeypatch.setattr(service, "scoped", lambda row, fence: row)
    monkeypatch.setattr(
        service, "read_completed_response", lambda *a: (request, response)
    )
    value = service.ProviderSocketCall(
        schema_version="mineagent.oe-structured-provider-broker-call.v1",
        admission_id=admission,
        process_ticket="a" * 64,
        context_ticket=service.context_ticket(key, context),
        context=context,
        request=request,
        request_sha256=request.sha256,
    )

    def execute(raw=None):
        return service.handle_provider_socket(
            NS(kw={"bind": object()}),
            NS(),
            canonical_bytes(value) if raw is None else raw,
            NS(storage_root="/unused"),
            channel,
            running=running,
            ticket_key=key,
        )

    return NS(**locals())


def test_actual_sdk_bridge_outside_transactions_and_native_lock(monkeypatch):
    c = arranged(monkeypatch)
    raw = c.execute()
    assert c.calls == ["authenticate", "commit", "sdk", "commit"]
    assert str(c.exchange.id).encode() in raw
    assert c.value.process_ticket.encode() not in raw


@pytest.mark.parametrize("field", ["admission_id", "context_ticket", "request_sha256"])
def test_changed_socket_commitment_rejected_before_sdk(monkeypatch, field):
    c = arranged(monkeypatch)
    values = c.value.model_dump(mode="json")
    values[field] = str(uuid4()) if field == "admission_id" else "f" * 64
    with pytest.raises(ProcessError):
        c.execute(canonical_bytes(values))
    assert "sdk" not in c.calls


def test_socket_noncanonical_json_rejected_before_authentication(monkeypatch):
    c = arranged(monkeypatch)
    with pytest.raises(ProcessError, match="canonical"):
        c.execute(b" " + canonical_bytes(c.value))
    assert not c.calls


def test_post_sdk_scope_change_cannot_return_success(monkeypatch):
    c = arranged(monkeypatch)
    original = service.submit_provider_chat

    def changed(*a, **kw):
        result = original(*a, **kw)
        c.state.changed = True
        return result

    monkeypatch.setattr(service, "submit_provider_chat", changed)
    with pytest.raises(ProcessError, match="changed"):
        c.execute()
    assert c.calls == ["authenticate", "commit", "sdk"]


def test_post_sdk_commit_failure_cannot_return_success(monkeypatch):
    c = arranged(monkeypatch)
    original = service.submit_provider_chat

    def changed(*a, **kw):
        result = original(*a, **kw)
        c.state.fail_commit = True
        return result

    monkeypatch.setattr(service, "submit_provider_chat", changed)
    with pytest.raises(ProcessError, match="commit_failed"):
        c.execute()
    assert c.calls == ["authenticate", "commit", "sdk"]


def test_post_sdk_fresh_orm_instance_is_compared_by_immutable_id(monkeypatch):
    c = arranged(monkeypatch)
    original = service.require_provider_operation

    def fresh(*a, **kw):
        value = original(*a, **kw)
        return NS(
            intent=NS(id=value.intent.id),
            config=value.config,
            context=value.context,
            request=value.request,
        )

    monkeypatch.setattr(service, "require_provider_operation", fresh)
    assert c.execute()


@pytest.mark.parametrize("kind", sorted(service._CONTROLLER))
def test_controller_events_cannot_nominate_worker_identity(kind):
    event = NS(kind=kind, payload={"grant": {"pid": 1}})
    assert service._actor(None, None, event, b"k" * 32) == (None, None)


def test_unknown_event_cannot_select_any_actor():
    with pytest.raises(ProcessError, match="socket_event"):
        service._actor(None, None, NS(kind="arbitrary", payload={}), b"k" * 32)


def test_worker_event_requires_real_descriptor_not_pid_claim():
    with pytest.raises(ValueError):
        service._actor(
            None,
            None,
            NS(kind="rendered_prompt", payload={"grant": {"pid": 1}}),
            b"k" * 32,
        )
