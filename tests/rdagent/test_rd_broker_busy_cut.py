from types import CodeType
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import rdagent_operational_broker as operational
from backend.app import rdagent_provider_broker as broker
from backend.app import rdagent_workflow_coordination as coordination
from backend.app import research_object_services as objects
from backend.app.services import DomainError
from packages.rdagent_provider.process_receipts import SDKIntent, SDKStarted, SDKStopped
from tests.integration.rd_broker_busy_cut import broker_busy_boundary

_SUBMIT = broker.submit_provider_request.__code__
_CALLBACK = next(
    value
    for value in _SUBMIT.co_consts
    if type(value) is CodeType and value.co_name == "identity_boundary"
)
_RECEIPT = broker._retain_receipt.__code__
_PUBLISH = operational._publish.__code__
_RESERVED = objects.publish_reserved_object.__code__


def _trace(*frames):
    trace = None
    for code, line, values in reversed(frames):
        trace = NS(
            tb_frame=NS(f_code=code, f_locals=values),
            tb_lineno=line,
            tb_next=trace,
        )
    return trace


@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        ([(_SUBMIT, 163, {})], None),
        ([(_SUBMIT, 182, {})], None),
        ([(_SUBMIT, 208, {})], "admit"),
        ([(_SUBMIT, 205, {}), (_PUBLISH, 109, {})], "reserve"),
        (
            [
                (_SUBMIT, 205, {}),
                (_PUBLISH, 139, {"previous": None}),
                (_RESERVED, 766, {}),
            ],
            "request_read",
        ),
        (
            [
                (_SUBMIT, 205, {}),
                (_PUBLISH, 139, {"previous": object()}),
                (_RESERVED, 766, {}),
            ],
            None,
        ),
        (
            [
                (_SUBMIT, 205, {}),
                (_PUBLISH, 139, {"previous": None}),
                (_RESERVED, 809, {}),
            ],
            None,
        ),
        (
            [
                (_SUBMIT, 249, {}),
                (_RECEIPT, 65, {"document": SDKIntent.model_construct()}),
            ],
            "intent",
        ),
        ([(_SUBMIT, 282, {}), (_CALLBACK, 255, {})], "started"),
        (
            [
                (_SUBMIT, 282, {}),
                (_CALLBACK, 274, {}),
                (_RECEIPT, 68, {"document": SDKStarted.model_construct()}),
            ],
            "started",
        ),
        (
            [
                (_SUBMIT, 282, {}),
                (_CALLBACK, 274, {}),
                (_RECEIPT, 68, {"document": SDKStopped.model_construct()}),
            ],
            "stop",
        ),
        (
            [
                (_SUBMIT, 282, {}),
                (_CALLBACK, 274, {}),
                (_RECEIPT, 68, {"document": "stop"}),
            ],
            None,
        ),
        ([(_SUBMIT, 307, {}), (_PUBLISH, 109, {})], "response_publish"),
        ([(_SUBMIT, 315, {})], "settle"),
        ([(_SUBMIT, 329, {})], None),
        ([(_SUBMIT, 208, {}), (_SUBMIT, 208, {})], None),
        ([(_SUBMIT.replace(), 208, {})], None),
    ],
)
def test_only_exact_boundaries_are_projected(frames, expected):
    assert broker_busy_boundary(_trace(*frames)) == expected


def test_source_drift_and_trace_overflow_fail_closed(monkeypatch, tmp_path):
    assert broker_busy_boundary(_trace(*[(_SUBMIT, 208, {})] * 65)) is None
    drift = tmp_path / "broker.py"
    drift.write_text("# private source")
    monkeypatch.setattr(broker, "__file__", str(drift))
    assert broker_busy_boundary(_trace((_SUBMIT, 208, {}))) is None


def test_real_shared_preflight_neutral_busy_does_not_infer_request_read(monkeypatch):
    gate = coordination._Gate()
    monkeypatch.setattr(coordination, "monotonic", lambda: 9)
    monkeypatch.setattr(broker, "parse_request", lambda *_: object())
    monkeypatch.setattr(broker, "canonical_bytes", lambda *_: b"private")
    monkeypatch.setattr(broker, "neutral_transaction", lambda *_, **__: gate.hold(9))
    with pytest.raises(DomainError) as caught:
        broker.submit_provider_request(
            object(),
            object(),
            call_id=uuid4(),
            idempotency_key=uuid4(),
            relation="initial",
            protocol="private",
            request_bytes=b"private",
            settings=object(),
            controller_peer=object(),
            original_deadline=9,
        )
    assert caught.value.code == "neutral_busy"
    assert broker_busy_boundary(caught.value.__traceback__) is None


def test_real_fresh_request_reserve_is_distinct_from_shared_preflight(monkeypatch):
    from contextlib import nullcontext

    gate = coordination._Gate()
    binding = object()
    operator = NS(connection=NS(secret_env="MINEAGENT_TEST_BROKER_CUT_KEY"))
    monkeypatch.setenv(operator.connection.secret_env, "synthetic-fixture")
    monkeypatch.setattr(coordination, "monotonic", lambda: 9)
    monkeypatch.setattr(broker, "time", NS(monotonic=lambda: 8))
    monkeypatch.setattr(
        broker, "parse_request", lambda *_: NS(model_profile_id="fixture")
    )
    monkeypatch.setattr(broker, "canonical_bytes", lambda *_: b"private")
    monkeypatch.setattr(
        broker,
        "neutral_transaction",
        lambda *_, **__: nullcontext(NS(scalar=lambda *_: None)),
    )
    monkeypatch.setattr(
        operational, "neutral_transaction", lambda *_, **__: gate.hold(9)
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_: None)
    monkeypatch.setattr(broker, "_scope", lambda *_: {})
    monkeypatch.setattr(
        broker, "require_research_spec", lambda *_: (None, NS(model_binding=binding))
    )
    monkeypatch.setattr(broker, "require_request_binding", lambda *_: None)
    monkeypatch.setattr(
        broker, "resolve_provider_declaration", lambda *_: (operator, binding)
    )
    monkeypatch.setattr(broker, "resolve_provider_runtime", lambda *_: None)
    with pytest.raises(DomainError) as caught:
        broker.submit_provider_request(
            object(),
            NS(owner_id=uuid4()),
            call_id=uuid4(),
            idempotency_key=uuid4(),
            relation="initial",
            protocol="private",
            request_bytes=b"private",
            settings=NS(rdagent_provider_python="synthetic-fixture"),
            controller_peer=object(),
            original_deadline=9,
        )
    assert caught.value.code == "neutral_busy"
    assert broker_busy_boundary(caught.value.__traceback__) == "reserve"
