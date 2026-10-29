"""Broker ordering with real wire DTOs and explicit DB/SDK doubles, not PG proof."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_broker as broker
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_contracts import OEChatResponse
from packages.rdagent_provider.transport import ProcessIdentity, ProcessStop
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_contracts import declared


def arranged(monkeypatch):
    profile, binding, domain, _, parent, rendered, context, _ = declared()
    from packages.openevolve_structured.provider_response import build_request

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
    events, documents = [], []
    state = NS(
        active=False,
        previous=None,
        membership=None,
        fail_sdk=False,
        missing_stop=False,
        fail_settle=False,
        changed=False,
    )
    fence = NS(
        owner_id=context.owner_id,
        run_id=context.run_id,
        attempt_id=uuid4(),
        generation=1,
    )
    peer = NS(_context=NS(deadline=broker.time.monotonic() + 30))
    config = NS(model_binding=binding, runtime="runtime")
    bound = NS(request=request, config=config)
    settings = NS(storage_root="/unused", oe_structured_provider_python="/fixed/python")
    factory = NS(kw={"bind": object()})
    member, transport = NS(id=uuid4()), NS(id=uuid4())
    exchange_id = uuid4()

    @contextmanager
    def guard(*args, **kwargs):
        yield

    @contextmanager
    def transaction(*args, **kwargs):
        assert not state.active
        state.active = True
        try:
            yield NS(
                scalar=lambda statement: (
                    state.previous
                    if "provider_exchanges" in str(statement)
                    else state.membership
                )
            )
        except BaseException:
            events.append("rollback")
            raise
        else:
            events.append("commit")
        finally:
            state.active = False

    def require(*args, **kwargs):
        assert state.active and kwargs["peer"] is peer
        events.append("revalidate")
        if state.changed:
            raise ValueError("changed")
        return bound

    def admit(*args, **kwargs):
        assert state.active
        events.append("submitted")
        state.membership = member
        return member, NS(), transport

    def publish(*args):
        assert not state.active
        events.append(args[3])
        return uuid4()

    def retain(*args):
        assert not state.active
        document = args[4]
        documents.append(document)
        events.append(document.kind)

    def sdk(*args, **kwargs):
        assert not state.active
        assert events.index("submitted") < events.index("intent")
        assert "commit" in events[events.index("submitted") : events.index("intent")]
        events.append("sdk")
        intent = documents[0]
        identity = ProcessIdentity(
            123, 42, 123, 123, 2, str(intent.boot_id), intent.deadline_monotonic_ns
        )
        kwargs["identity_boundary"]("started", identity)
        events.append("private_pipe")
        assert "started" in events
        if not state.missing_stop:
            kwargs["identity_boundary"](
                "stopped", ProcessStop(identity, intent.admitted_monotonic_ns + 1)
            )
        if state.fail_sdk:
            raise RuntimeError("sdk_unknown")
        return NS(response=response)

    def settle(*args, **kwargs):
        assert state.active
        events.append("settle_success" if kwargs.get("response") else "settle_unknown")
        if state.fail_settle or state.missing_stop:
            raise ValueError("no_complete_physical_chain")
        assert documents[-1].kind == "stopped"
        return exchange_id

    monkeypatch.setattr(broker, "shared_lifecycle", guard)
    monkeypatch.setattr(broker, "native_storage_guard", guard)
    monkeypatch.setattr(broker, "neutral_transaction", transaction)
    monkeypatch.setattr(broker, "require_provider_operation", require)
    monkeypatch.setattr(
        broker, "resolve_operator_profile", lambda *a: (profile, binding)
    )
    monkeypatch.setattr(
        broker, "resolve_runtime", lambda *a: NS(runtime=config.runtime)
    )
    monkeypatch.setattr(broker, "publish_evidence", publish)
    monkeypatch.setattr(broker, "retain_physical_receipt", retain)
    monkeypatch.setattr(broker, "admit_provider_transport", admit)
    monkeypatch.setattr(broker, "complete_chat", sdk)
    monkeypatch.setattr(broker, "_settle", settle)
    monkeypatch.setattr(
        broker, "validate_completed_usage", lambda req, res, bind: (res, None)
    )
    monkeypatch.setattr(
        broker, "read_completed_response", lambda *a: (request, response)
    )
    monkeypatch.setenv(profile.connection.secret_env, "local-test-only")

    def call(raw=None):
        return broker.submit_provider_chat(
            factory,
            fence,
            context.iteration_intent_id,
            canonical_bytes(request) if raw is None else raw,
            settings,
            peer=peer,
        )

    return NS(
        call=call,
        state=state,
        events=events,
        documents=documents,
        exchange_id=exchange_id,
        peer=peer,
    )


def test_submitted_and_identity_commit_before_private_request(monkeypatch):
    test = arranged(monkeypatch)
    assert test.call()[0] == test.exchange_id
    assert [d.kind for d in test.documents] == ["intent", "started", "stopped"]
    assert test.events.index("stopped") < test.events.index("settle_success")


def test_completed_replay_never_resubmits(monkeypatch):
    test = arranged(monkeypatch)
    test.state.previous = NS(id=test.exchange_id)
    assert test.call()[0] == test.exchange_id
    assert "sdk" not in test.events and "submitted" not in test.events


def test_partial_transport_cannot_be_replaced(monkeypatch):
    test = arranged(monkeypatch)
    test.state.membership = NS(id=uuid4())
    with pytest.raises(ValueError, match="cannot be submitted again"):
        test.call()
    assert "sdk" not in test.events and "submitted" not in test.events


def test_unknown_sdk_retains_original_error_and_full_stop_settlement(monkeypatch):
    test = arranged(monkeypatch)
    test.state.fail_sdk = True
    with pytest.raises(RuntimeError, match="sdk_unknown"):
        test.call()
    assert "settle_unknown" in test.events and "settle_success" not in test.events


def test_missing_stop_remains_uncertain_not_success(monkeypatch):
    test = arranged(monkeypatch)
    test.state.fail_sdk = test.state.missing_stop = True
    with pytest.raises(RuntimeError, match="sdk_unknown") as error:
        test.call()
    assert error.value.__notes__ == [
        "Structured provider physical settlement remains uncertain"
    ]
    assert [d.kind for d in test.documents] == ["intent", "started"]


def test_wire_substitution_rejected_before_any_submission(monkeypatch):
    test = arranged(monkeypatch)
    with pytest.raises(ProcessError):
        test.call(b"{}")
    assert "submitted" not in test.events and "sdk" not in test.events


def test_expired_original_deadline_never_starts_sdk(monkeypatch):
    test = arranged(monkeypatch)
    test.peer._context.deadline = broker.time.monotonic() - 1
    with pytest.raises(ProcessError):
        test.call()
    assert "submitted" in test.events  # truthful conservative unresolved obligation
    assert "sdk" not in test.events and not test.documents


def settlement_case(monkeypatch):
    from backend.app import oe_structured_provider_evidence as evidence
    from tests.openevolve.test_structured_provider_evidence import fixture

    case = fixture(monkeypatch)
    case.transport.state = "submitted"
    case.operation.state = "open"
    case.reservation.state = "reserved"
    case.added = []
    case.charges = []
    case.session.add = case.added.append
    case.session.flush = lambda: None
    monkeypatch.setattr(
        broker, "require_neutral_transaction", lambda *a, **kw: (NS(), NS())
    )
    monkeypatch.setattr(broker, "decode_spec", lambda *a: (NS(), case.config))
    monkeypatch.setattr(broker, "read_research_object", evidence.read_research_object)
    monkeypatch.setattr(
        broker, "settle_budget", lambda *a, **kw: case.charges.append(kw)
    )
    return case


def test_unknown_settlement_requires_actual_three_receipt_bytes(monkeypatch):
    case = settlement_case(monkeypatch)
    assert broker._settle(case.session, case.fence, case.membership.id, None) is None
    assert case.operation.state == case.transport.state == "unknown"
    assert len(case.charges) == 1
    assert case.charges[0]["outcome"] == "conservative_unknown"
    assert case.charges[0]["charge"].model_dump() == case.reservation.amounts
    assert len(case.added) == 1 and case.added[0].response_object_id is None


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("transport", "ordinal", 2),
        ("operation", "purpose", "embedding"),
        ("operation", "ordinal", 1),
        ("operation", "request_object_kind", "rd_model_request"),
        ("operation", "request_sha256", "0" * 64),
        ("operation", "request", {}),
        ("reservation", "state", "settled"),
        ("reservation", "purpose", "process_unit"),
        ("membership", "model_binding_sha256", "0" * 64),
    ],
)
def test_settlement_rejects_wrong_operation_or_accounting_before_charge(
    monkeypatch, target, field, value
):
    case = settlement_case(monkeypatch)
    setattr(getattr(case, target), field, value)
    with pytest.raises(ProcessError):
        broker._settle(case.session, case.fence, case.membership.id, None)
    assert not case.charges and not case.added


def test_settlement_rejects_hidden_physical_retry(monkeypatch):
    case = settlement_case(monkeypatch)
    case.multiples[broker.ResearchModelTransport].append(NS(id=uuid4()))
    with pytest.raises(ProcessError):
        broker._settle(case.session, case.fence, case.membership.id, None)
    assert not case.charges


def test_settlement_never_invents_missing_stop(monkeypatch):
    case = settlement_case(monkeypatch)
    case.receipts.pop()
    with pytest.raises(ProcessError):
        broker._settle(case.session, case.fence, case.membership.id, None)
    assert not case.charges and not case.added
