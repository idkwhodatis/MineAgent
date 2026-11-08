"""Observer forwarding/privacy tests, never populated receipt/runtime evidence."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "integration"))
    return importlib.import_module("oe_receipt_cost_diagnostic")


@pytest.mark.parametrize("kind", ["intent", "started", "stopped", "PRIVATE"])
def test_receipt_exactly_once_same_values_and_no_payload_output(monkeypatch, kind):
    d = module(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker
    from backend.app import research_object_services as objects
    from runtimes.openevolve_operational import daemon_identity

    document = NS(kind=kind, deadline_monotonic_ns=123456, private="PRIVATE")
    args, kwargs, calls, output = (
        (1, 2, 3, 4, document, "PRIVATE"),
        {"private": "PRIVATE"},
        [],
        [],
    )
    result = object()

    def original(*a, **k):
        calls.append((a, k))
        return result

    def forbidden(*a, **k):
        raise AssertionError("Observer attempted extra I/O")

    monkeypatch.setattr(broker, "retain_physical_receipt", original)
    monkeypatch.setattr(objects, "publish_bytes", forbidden)
    monkeypatch.setattr(daemon_identity, "_observe", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    with d.diagnose_receipt_cost(lambda *a: output.append(a)):
        assert broker.retain_physical_receipt(*args, **kwargs) is result
    assert broker.retain_physical_receipt is original and calls == [(args, kwargs)]
    assert document.deadline_monotonic_ns == 123456
    marker, report = output[0]
    assert marker == "OE_RECEIPT_COST_V1:"
    assert len(report["summary"]) == 1 and report["summary"][0]["count"] == 1
    assert report["summary"][0]["phase"] == (
        kind + "_receipt" if kind != "PRIVATE" else "other_receipt"
    )
    assert "PRIVATE" not in json.dumps(output)


@pytest.mark.parametrize("failure", [ValueError, RuntimeError, KeyboardInterrupt])
def test_identical_exception_and_all_listeners_restored(monkeypatch, failure):
    d = module(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    error, output, listeners = failure("PRIVATE"), [], []

    def original(*args, **kwargs):
        raise error

    monkeypatch.setattr(broker, "retain_physical_receipt", original)
    listen = d.event.listen

    def register(owner, name, callback):
        listeners.append((owner, name, callback))
        listen(owner, name, callback)

    monkeypatch.setattr(d.event, "listen", register)
    with (
        pytest.raises(failure) as caught,
        d.diagnose_receipt_cost(lambda *a: output.append(a)),
    ):
        assert len(listeners) == 6 and all(
            event.contains(*entry) for entry in listeners
        )
        broker.retain_physical_receipt(1, 2, 3, 4, NS(kind="intent"), 6)
    assert caught.value is error and broker.retain_physical_receipt is original
    assert all(not event.contains(*entry) for entry in listeners)
    assert output[0][1]["summary"][0]["failures"] == 1
    assert "PRIVATE" not in json.dumps(output)


def test_original_sdk_deadline_and_boundary_arguments_unchanged(monkeypatch):
    d = module(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    seen, output, evidence, result = [], [], object(), object()
    original_error = ValueError("PRIVATE")

    def boundary(name, value):
        seen.append((name, value))
        if name == "stopped":
            raise original_error
        return result

    def sdk(*args, **kwargs):
        assert args == (evidence,)
        assert kwargs["deadline_monotonic"] == 8.125
        assert kwargs["secret"] == "PRIVATE"
        assert kwargs["identity_boundary"]("started", evidence) is result
        kwargs["identity_boundary"]("stopped", evidence)

    monkeypatch.setattr(broker, "complete_chat", sdk)
    with (
        pytest.raises(ValueError) as caught,
        d.diagnose_receipt_cost(lambda *a: output.append(a)),
    ):
        broker.complete_chat(
            evidence,
            deadline_monotonic=8.125,
            secret="PRIVATE",
            identity_boundary=boundary,
        )
    assert caught.value is original_error
    assert seen == [("started", evidence), ("stopped", evidence)]
    assert broker.complete_chat is sdk
    assert {row["phase"] for row in output[0][1]["summary"]} == {
        "sdk",
        "started_boundary",
        "stopped_boundary",
    }
    assert "PRIVATE" not in json.dumps(output)


def test_commit_segments_survive_helper_return_and_timing_is_inclusive(monkeypatch):
    d = module(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker
    from backend.app import oe_structured_provider_publication as pub
    from backend.app import research_object_services as objects

    recorder, output = d.Recorder(), []
    monkeypatch.setattr(d, "Recorder", lambda: recorder)

    class Transaction:
        pass

    session = Transaction()

    def commit():
        recorder.before_commit(session)
        recorder.after_commit(session)

    def no_op(*a, **k):
        return None

    def publish(*a, **k):
        commit()  # publication preflight transaction
        objects.finalize_object()
        commit()  # after finalization helper returned

    def evidence(*a, **k):
        commit()  # reservation transaction
        pub.publish_reserved_object()

    def retain(*a, **k):
        pub.publish_evidence()
        pub.bind_physical_receipt()
        commit()  # after binding helper returned

    monkeypatch.setattr(objects, "finalize_object", no_op)
    monkeypatch.setattr(pub, "publish_reserved_object", publish)
    monkeypatch.setattr(pub, "publish_evidence", evidence)
    monkeypatch.setattr(pub, "bind_physical_receipt", no_op)
    monkeypatch.setattr(broker, "retain_physical_receipt", retain)
    with d.diagnose_receipt_cost(lambda *a: output.append(a)):
        broker.retain_physical_receipt(1, 2, 3, 4, NS(kind="intent"), 6)
    rows = output[0][1]["summary"]
    assert {
        row["component"] for row in rows if row["component"].startswith("commit_")
    } == {
        "commit_reservation",
        "commit_publication_preflight",
        "commit_finalization",
        "commit_binding",
    }
    assert all(row["count"] == 1 for row in rows)
    assert recorder.phase.get() is None and recorder.segment.get() == "other"
    assert output[0][1]["inclusive_overlapping"] is True


def test_cursor_events_never_render_sql_parameters_errors_and_clean_up(monkeypatch):
    d = module(monkeypatch)
    r = d.Recorder()

    class Context:
        pass

    first, second = Context(), Context()
    r.phase.set("intent_receipt")
    r.before_cursor(None, None, "PRIVATE SQL", {"secret": "PRIVATE"}, first, False)
    r.after_cursor(None, None, "PRIVATE SQL", {"secret": "PRIVATE"}, first, False)
    r.before_cursor(None, None, "PRIVATE SQL", None, second, False)
    r.handle_error(
        NS(execution_context=second, original_exception=RuntimeError("PRIVATE"))
    )
    r.handle_error(NS(original_exception=RuntimeError("PRIVATE")))
    assert len(r.cursors) == 0
    assert r.report()["summary"][0]["count"] == 2
    assert r.report()["summary"][0]["failures"] == 1
    assert "PRIVATE" not in json.dumps(r.report())


def test_counts_times_labels_and_tracking_memory_are_bounded(monkeypatch):
    d = module(monkeypatch)
    r = d.Recorder()
    monkeypatch.setattr(d, "MAX_COUNT", 2)
    monkeypatch.setattr(d, "monotonic_ns", lambda: d.MAX_NS * 2)
    for _ in range(4):
        r.record("retain_receipt", 0, False, "intent_receipt")
        r.record("PRIVATE", 0, False, "intent_receipt")
        r.record("retain_receipt", 0, False, "PRIVATE")
    assert r.report()["overflow"] is True
    row = r.report()["summary"][0]
    assert row["count"] == row["failures"] == 2
    assert row["inclusive_total_ms"] == row["max_ms"] == d.MAX_NS / 1e6

    class Context:
        pass

    contexts = [Context() for _ in range(4)]
    r.phase.set("started_receipt")
    for context in contexts:
        r.before_cursor(None, None, None, None, context, False)
        r.before_commit(context)
    assert len(r.cursors) == len(r.commits) == 2
    for phase in d.PHASES:
        for component in d.COMPONENTS:
            r.record(component, 0, True, phase)
    assert len(r.totals) == len(d.PHASES) * len(d.COMPONENTS)
    assert len(json.dumps(r.report())) < 65536
    assert "PRIVATE" not in json.dumps(r.report())


def test_clock_and_output_failure_never_replace_original(monkeypatch):
    d = module(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker

    error, calls = RuntimeError("original"), []

    def original(*a, **k):
        calls.append(1)
        raise error

    def broken(*a, **k):
        raise ValueError("PRIVATE")

    monkeypatch.setattr(broker, "retain_physical_receipt", original)
    monkeypatch.setattr(d, "monotonic_ns", broken)
    with pytest.raises(RuntimeError) as caught, d.diagnose_receipt_cost(broken):
        broker.retain_physical_receipt(1, 2, 3, 4, NS(kind="intent"), 6)
    assert caught.value is error and calls == [1]
    assert broker.retain_physical_receipt is original


def test_pre_sdk_operation_has_separate_phase_and_no_timing_io(monkeypatch):
    d = module(monkeypatch)
    from backend.app import oe_structured_provider_broker as broker
    from backend.app import oe_structured_provider_operation as operation

    output = []
    monkeypatch.setattr(operation, "require_parent_candidate", lambda: "same")
    monkeypatch.setattr(
        broker,
        "require_provider_operation",
        lambda: operation.require_parent_candidate(),
    )
    with d.diagnose_receipt_cost(lambda *a: output.append(a)):
        assert broker.require_provider_operation() == "same"
    rows = output[0][1]["summary"]
    assert {row["phase"] for row in rows} == {"pre_sdk_operation"}
    assert {row["component"] for row in rows} == {
        "provider_operation",
        "parent_candidate",
    }
