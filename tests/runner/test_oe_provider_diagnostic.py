"""Closed observer privacy and exact forwarding; no SDK/process/network calls."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

PRIVATE = "private_payload_secret_id"


def module():
    path = Path(__file__).parents[1] / "integration/oe_provider_diagnostic.py"
    spec = importlib.util.spec_from_file_location("oe_provider_probe", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def arranged(monkeypatch, *, wire_error=None, validation_error=None):
    from packages.openevolve_structured import provider_transport as oe
    from packages.rdagent_provider import transport

    diag = module()
    result, process, request = object(), object(), object()
    calls, emitted = [], []
    raw = b'{"ok":true,"response":{"private_payload_secret_id":"private"}}'

    def wire(*args, **kwargs):
        assert args == (process, request) and kwargs == {"deadline": 18.0}
        calls.append("wire")
        if wire_error is not None:
            raise wire_error
        return raw

    def validate(*args, **kwargs):
        assert args == (raw, request) and kwargs == {"synthetic": True}
        calls.append("validate")
        if validation_error is not None:
            raise validation_error
        return result

    def execute(value, **kwargs):
        assert value is request and kwargs == {"deadline_monotonic": 18.0}
        observed = transport._wire_exchange(process, value, deadline=18.0)
        assert observed is raw
        return transport._validated_result(observed, value, synthetic=True)

    monkeypatch.setattr(oe, "execute", execute)
    monkeypatch.setattr(transport, "_wire_exchange", wire)
    monkeypatch.setattr(transport, "_validated_result", validate)
    ticks = iter([10.0, 11.0, 11.25] * 20)
    monkeypatch.setattr(diag, "time", NS(monotonic=lambda: next(ticks)))
    return NS(
        diag=diag,
        oe=oe,
        transport=transport,
        execute=execute,
        wire=wire,
        validate=validate,
        result=result,
        request=request,
        calls=calls,
        emitted=emitted,
        emit=lambda marker, row: emitted.append((marker, row)),
    )


def test_exact_alias_wiring_preserves_bytes_arguments_result_and_restores(monkeypatch):
    case = arranged(monkeypatch)
    classify = case.diag.envelope

    def inspect_after_wire(raw):
        case.diag.time.monotonic()  # Observation time must not inflate wire timing.
        return classify(raw)

    monkeypatch.setattr(case.diag, "envelope", inspect_after_wire)
    with case.diag.diagnose_provider(case.emit, [PRIVATE]):
        assert case.oe.execute(case.request, deadline_monotonic=18.0) is case.result
    assert case.calls == ["wire", "validate"]
    assert case.oe.execute is case.execute
    assert case.transport._wire_exchange is case.wire
    assert case.transport._validated_result is case.validate
    marker, result = case.emitted[0]
    assert marker == "OE_PROVIDER_PROGRESS_V1:"
    assert result == {
        "count": 1,
        "overflow": False,
        "synthetic_http_count": 1,
        "calls": [
            {
                "entry_remaining_ms": 8000,
                "wire": "returned",
                "wire_remaining_ms": 7000,
                "wire_elapsed_ms": 250,
                "validation": "accepted",
                "envelope": "success_envelope",
            }
        ],
    }
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize(
    "error,category",
    [
        (TimeoutError(PRIVATE), "timeout"),
        (BrokenPipeError(PRIVATE), "broken_pipe"),
        (OSError(PRIVATE), "os_error"),
        (ValueError(PRIVATE), "value"),
        (ValueError("child_input_closed"), "input_closed"),
        (ValueError("child_output_limit"), "output_limit"),
        (RuntimeError(PRIVATE), "other"),
        (KeyboardInterrupt(PRIVATE), "cancelled"),
    ],
)
def test_wire_errors_rethrow_same_instance_and_never_format_messages(
    monkeypatch, error, category
):
    case = arranged(monkeypatch, wire_error=error)
    with pytest.raises(type(error)) as caught, case.diag.diagnose_provider(case.emit):
        case.oe.execute(case.request, deadline_monotonic=18.0)
    assert caught.value is error and case.calls == ["wire"]
    row = case.emitted[0][1]["calls"][0]
    assert row["wire"] == "failed" and row["wire_failure"] == category
    assert row["validation"] == "not_entered"
    assert PRIVATE not in json.dumps(case.emitted)
    assert case.oe.execute is case.execute


def test_validation_is_distinct_from_wire_failure(monkeypatch):
    error = ValueError(PRIVATE)
    case = arranged(monkeypatch, validation_error=error)
    with pytest.raises(ValueError) as caught, case.diag.diagnose_provider(case.emit):
        case.oe.execute(case.request, deadline_monotonic=18.0)
    assert caught.value is error
    row = case.emitted[0][1]["calls"][0]
    assert row["wire"] == "returned" and row["validation"] == "failed"
    assert row["validation_failure"] == "value"
    assert PRIVATE not in json.dumps(row)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"", "empty"),
        (b"not json private_payload_secret_id", "invalid"),
        (b'{"ok":false,"error_class":"provider_outcome_unknown"}', "child_rejected"),
        (b'{"ok":false,"error_class":"provider_outcome_unknown_private"}', "other"),
        (b'{"ok":false,"error_class":"private_payload_secret_id"}', "other"),
        (b'{"ok":true,"response":"private_payload_secret_id"}', "success_envelope"),
        (b'{"ok":true,"response":{},"private_payload_secret_id":1}', "other"),
        (b'{"ok":true,"ok":false,"response":{}}', "invalid"),
        (b"x" * 1048577, "other"),
    ],
)
def test_envelope_is_closed_and_never_returns_payload(raw, expected):
    assert module().envelope(raw) == expected


def test_counts_bounded_and_shared_non_oe_calls_unobserved(monkeypatch):
    case = arranged(monkeypatch)
    with case.diag.diagnose_provider(case.emit, [PRIVATE] * 100):
        # Same shared helpers used outside the OE execute alias must pass through.
        case.execute(case.request, deadline_monotonic=18.0)
        for _ in range(12):
            case.oe.execute(case.request, deadline_monotonic=18.0)
    result = case.emitted[0][1]
    assert result["count"] == 9 and result["overflow"] is True
    assert len(result["calls"]) == 8 and result["synthetic_http_count"] == 17
    assert len(case.calls) == 26
    assert PRIVATE not in json.dumps(result)


def test_metadata_and_emitter_failures_do_not_change_original_failure(monkeypatch):
    error = ValueError(PRIVATE)
    case = arranged(monkeypatch, wire_error=error)

    def broken(*args, **kwargs):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(case.diag, "time", NS(monotonic=broken))
    monkeypatch.setattr(case.diag, "exception_family", broken)
    with pytest.raises(ValueError) as caught, case.diag.diagnose_provider(broken, []):
        case.oe.execute(case.request, deadline_monotonic=18.0)
    assert caught.value is error and case.oe.execute is case.execute
    assert case.transport._wire_exchange is case.wire


def test_signed_deadline_and_unknown_time_are_not_fabricated():
    diag = module()
    assert diag.milliseconds(9.0, 10.0, signed=True) == -1000
    assert diag.milliseconds(10**6, 0, signed=True) == 600000
    assert diag.milliseconds(-(10**6), 0, signed=True) == -600000
    assert diag.milliseconds(float("nan"), 1) == "unknown"
    assert diag.milliseconds(None, 1) == "unknown"
    assert diag.synthetic_count(object()) == "unknown"
    for message in ("child_input_closed", "child_output_limit"):
        for private in (message + PRIVATE, PRIVATE + message, message + "\n" + PRIVATE):
            assert diag.wire_failure(ValueError(private)) == "value"


def test_normal_fixture_passes_only_existing_in_memory_counter():
    root = Path(__file__).parents[1] / "integration"
    normal = (root / "test_oe_structured_normal_flow.py").read_text()
    queue = (root / "oe_queue_diagnostic.py").read_text()
    assert "synthetic_calls=oe_calls" in normal
    assert "diagnose_provider(emit, synthetic_calls)" in queue
    assert "queue.run_worker(" in normal and "wait=False" in normal
