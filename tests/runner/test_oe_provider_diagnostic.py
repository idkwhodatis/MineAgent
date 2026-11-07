"""SDK observer privacy and forwarding only; no PostgreSQL/runtime pass claim."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from packages.rdagent_provider.transport import ProviderFailure


def load(monkeypatch):
    root = Path(__file__).parents[1] / "integration"
    monkeypatch.syspath_prepend(str(root))
    from oe_queue_diagnostic import failure

    spec = importlib.util.spec_from_file_location(
        "provider_diagnostic_test", root / "oe_provider_diagnostic.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, failure


def test_receipt_persistence_expiry_is_visible_without_refreshing_deadline(monkeypatch):
    d, failure = load(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    now, outputs, calls = [0], [], []
    document = NS(kind="intent", deadline_monotonic_ns=8_000_000_000)
    error = ProviderFailure("provider_input_rejected", outcome_unknown=False)

    def retain(*args, **kwargs):
        calls.append((args, kwargs))
        now[0] = 9_000_000_000
        return document

    def complete(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["deadline_monotonic"] == 8.0
        raise error

    monkeypatch.setattr(broker, "retain_physical_receipt", retain)
    monkeypatch.setattr(broker, "complete_chat", complete)
    monkeypatch.setattr(d.time, "monotonic_ns", lambda: now[0])
    with d.diagnose_provider(lambda *args: outputs.append(args), failure):
        assert broker.retain_physical_receipt(1, 2, 3, 4, document, 6) is document
        with pytest.raises(ProviderFailure) as caught:
            broker.complete_chat("synthetic_private", deadline_monotonic=8.0)
        assert caught.value is error
    assert len(calls) == 2
    assert broker.complete_chat is complete and broker.retain_physical_receipt is retain
    marker, payload = outputs[0]
    assert marker == "OE_PROVIDER_PROGRESS_V1:"
    assert payload == {
        "count": 2,
        "overflow": False,
        "records": [
            {
                "phase": "intent_receipt",
                "remaining_ms": 8000,
                "completed": True,
                "elapsed_ms": 9000,
            },
            {
                "phase": "sdk",
                "remaining_ms": 0,
                "completed": False,
                "elapsed_ms": 0,
                "failure": {
                    "family": "provider",
                    "code": "provider_input_rejected",
                    "stage": "unknown",
                },
            },
        ],
    }
    assert "synthetic_private" not in json.dumps(outputs)


def test_boundary_error_observed_before_transport_sanitizes_it(monkeypatch):
    d, failure = load(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    original = ValueError("synthetic_private")
    sanitized = ProviderFailure("provider_stop_receipt_unconfirmed", contained=True)
    identity, seen, outputs = object(), [], []

    def boundary(event, evidence):
        seen.append((event, evidence))
        raise original

    def complete(*args, **kwargs):
        assert args == (identity,)
        assert kwargs["secret"] == "synthetic_private"
        assert kwargs["deadline_monotonic"] == 8.0
        try:
            kwargs["identity_boundary"]("started", identity)
        except ValueError as caught:
            assert caught is original
            raise sanitized from None

    monkeypatch.setattr(broker, "complete_chat", complete)
    monkeypatch.setattr(d.time, "monotonic_ns", lambda: 0)
    with d.diagnose_provider(lambda *args: outputs.append(args), failure):
        with pytest.raises(ProviderFailure) as caught:
            broker.complete_chat(
                identity,
                secret="synthetic_private",
                identity_boundary=boundary,
                deadline_monotonic=8.0,
            )
        assert caught.value is sanitized
    assert seen == [("started", identity)]
    records = outputs[0][1]["records"]
    assert records[0]["failure"]["code"] == "provider_stop_receipt_unconfirmed"
    assert records[1]["phase"] == "started_boundary"
    assert records[1]["failure"] == {
        "family": "value",
        "code": "unknown",
        "stage": "unknown",
        "category": "other",
    }
    assert "synthetic_private" not in json.dumps(outputs)


def test_successful_callbacks_and_result_are_forwarded_exactly_once(monkeypatch):
    d, failure = load(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    result, evidence, seen, outputs = object(), object(), [], []

    def boundary(event, value):
        seen.append((event, value))
        return result

    def complete(*args, **kwargs):
        assert kwargs["identity_boundary"]("started", evidence) is result
        assert kwargs["identity_boundary"]("stopped", evidence) is result
        return result

    monkeypatch.setattr(broker, "complete_chat", complete)
    with d.diagnose_provider(lambda *args: outputs.append(args), failure):
        assert broker.complete_chat(identity_boundary=boundary) is result
    assert seen == [("started", evidence), ("stopped", evidence)]
    assert all(record["completed"] for record in outputs[0][1]["records"])


def test_unknown_fields_and_repeated_calls_remain_closed_and_bounded(monkeypatch):
    d, failure = load(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    private, outputs = "synthetic_private", []
    monkeypatch.setattr(broker, "retain_physical_receipt", lambda *a: None)

    def complete(**kwargs):
        return kwargs["identity_boundary"](private, NS(secret=private))

    monkeypatch.setattr(broker, "complete_chat", complete)
    with d.diagnose_provider(lambda *args: outputs.append(args), failure):
        for _ in range(20):
            broker.retain_physical_receipt(None, None, None, None, NS(kind=private))
        broker.complete_chat(
            identity_boundary=lambda *args: None, deadline_monotonic=float("nan")
        )
    value = outputs[0][1]
    assert value["count"] == 22 and value["overflow"] and len(value["records"]) == 16
    assert value["records"][-1]["phase"] == "other_boundary"
    assert "synthetic_private" not in json.dumps(outputs)
    assert d.remaining_ms(True, 0) is None
    assert d.remaining_ms(0, True) is None
    assert d.remaining_ms(700_000_000_000, 0) == 600000


def test_observer_failure_does_not_replace_underlying_error(monkeypatch):
    d, _ = load(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    original = KeyboardInterrupt("synthetic_private")

    def broken(*args):
        raise ValueError("synthetic_private")

    def complete(*args, **kwargs):
        raise original

    monkeypatch.setattr(broker, "complete_chat", complete)
    with (
        pytest.raises(KeyboardInterrupt) as caught,
        d.diagnose_provider(broken, broken),
    ):
        broker.complete_chat()
    assert caught.value is original and broker.complete_chat is complete


def test_observer_clock_failure_does_not_prevent_call(monkeypatch):
    d, failure = load(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    sentinel, calls = object(), []

    def complete(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    def broken():
        raise OSError("synthetic_private")

    monkeypatch.setattr(broker, "complete_chat", complete)
    monkeypatch.setattr(d.time, "monotonic_ns", broken)
    with d.diagnose_provider(lambda *args: pytest.fail("no records"), failure):
        assert broker.complete_chat(sentinel, identity_boundary=None) is sentinel
    assert calls == [((sentinel,), {"identity_boundary": None})]


def test_no_provider_no_extra_output(monkeypatch):
    d, failure = load(monkeypatch)
    with d.diagnose_provider(lambda *args: pytest.fail("unexpected output"), failure):
        pass
