"""Offline failure projections; no live socket, kernel, Docker or DB proof."""

import json
from types import SimpleNamespace as NS

import pytest

from backend.app import openevolve_failure_diagnostic as diag
from backend.app import openevolve_socket_server as server
from tests.openevolve.test_operational_socket_server import initial, wire


@pytest.mark.parametrize("value", [None, True, {}, [], "private-token", 1])
def test_event_projection_unknown(value):
    assert diag.event_label(value) == "unknown"


@pytest.mark.parametrize(
    "value,want",
    [
        (float("nan"), "unknown"),
        (float("inf"), "unknown"),
        (True, "unknown"),
        (-1, "negative"),
        (0, "le_5"),
        (95, "le_95"),
        (601, "over_600"),
    ],
)
def test_time_is_finite_bucket(value, want):
    assert diag.seconds_bucket(value) == want


def test_hostile_error_has_no_property_string_or_metaclass_access():
    class Meta(type):
        def __hash__(self):
            raise AssertionError("hash hook")

        def __eq__(self, other):
            raise AssertionError("equality hook")

    class Hostile(Exception, metaclass=Meta):
        def __getattribute__(self, name):
            raise AssertionError("attribute hook")

        def __str__(self):
            raise AssertionError("string hook")

    assert diag.exception_label(Hostile()) == "other"


def test_auth_failure_is_original_and_kind_is_explicitly_untrusted(monkeypatch, capsys):
    unit = initial()
    channel = NS(close=lambda: None)

    # NS is unhashable; real socket ownership only needs a close method here.
    class Channel:
        def close(self):
            pass

    channel = Channel()
    raw = wire(unit, "checkpoint_publish")
    monkeypatch.setattr(server, "_raw_frame", lambda *args: raw)
    failure = ValueError("PRIVATE ticket/identity/source")

    def reject(*args, **kwargs):
        raise failure

    monkeypatch.setattr(server, "authenticate_socket", reject)
    with pytest.raises(ValueError) as got:
        unit._handle_in_window(channel, "events")
    assert got.value is failure
    assert not unit._connections
    data = unit.failure_diagnostic()
    assert data["authenticated_last_event"] == "none"
    assert data["first_handler_failure"] == {
        "elapsed_seconds_bucket": "le_5",
        "remaining_seconds_bucket": "le_5",
        "phase": "auth",
        "untrusted_event_kind": "checkpoint_publish",
        "exception": "value",
    }
    diag.emit_failure("before_cleanup", unit, unit.running, failure)
    raw = capsys.readouterr().out
    assert "PRIVATE" not in raw and str(unit.epoch_id) not in raw
    assert json.loads(raw.split(":", 1)[1])["stage"] == "before_cleanup"
    assert (
        json.loads(raw.split(":", 1)[1])["first_handler_failure"]
        == data["first_handler_failure"]
    )


def test_frame_failure_does_not_promote_arbitrary_event(monkeypatch):
    unit = initial()

    class Channel:
        def close(self):
            pass

    monkeypatch.setattr(server, "_raw_frame", lambda *args: b"private-invalid-json")
    with pytest.raises(ValueError):
        unit._handle_in_window(Channel(), "events")
    assert (
        unit.failure_diagnostic()["first_handler_failure"]["untrusted_event_kind"]
        == "none"
    )


def test_projection_caps_and_cleanup_has_only_finite_fields(capsys):
    unit = initial()
    unit._requests = 99999
    unit._acknowledgements = True
    unit._last_event = "private-token"
    data = unit.failure_diagnostic()
    assert data["requests"] == server.MAX_REQUESTS
    assert data["acknowledgements"] is None
    assert data["authenticated_last_event"] == "unknown"
    diag.emit_failure(
        "after_cleanup",
        unit,
        NS(
            measured_exit_code="private",
            measured_oom_killed="private",
            finished="private",
        ),
        ValueError(),
    )
    raw = capsys.readouterr().out
    assert "private" not in raw
    assert json.loads(raw.split(":", 1)[1]) == {
        "stage": "after_cleanup",
        "exit_code": None,
        "oom_killed": None,
        "stop_confirmed": False,
    }


def test_diagnostic_failure_cannot_escape(monkeypatch):
    def broken():
        raise KeyboardInterrupt("private")

    diag.emit_failure(
        "before_cleanup", NS(failure_diagnostic=broken), None, ValueError()
    )
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private")),
    )
    diag.emit_failure("after_cleanup", None, None, ValueError())


def test_recording_failure_does_not_replace_original_handler_error(monkeypatch):
    unit = initial()

    class Channel:
        def close(self):
            pass

    failure = ValueError("original-private")

    def reject(*args, **kwargs):
        raise failure

    monkeypatch.setattr(server, "_raw_frame", reject)
    monkeypatch.setattr(
        diag,
        "event_label",
        lambda value: (_ for _ in ()).throw(RuntimeError("diagnostic-private")),
    )
    with pytest.raises(ValueError) as got:
        unit._handle_in_window(Channel(), "events")
    assert got.value is failure


@pytest.mark.parametrize(
    "attack", ["extra", "nested_extra", "phase", "event", "exception", "bucket"]
)
def test_sink_reprojects_malformed_or_private_progress(capsys, attack):
    unit = initial()
    data = unit.failure_diagnostic()
    data["first_handler_failure"] = {
        "elapsed_seconds_bucket": "le_5",
        "remaining_seconds_bucket": "le_5",
        "phase": "auth",
        "untrusted_event_kind": "broker",
        "exception": "value",
    }
    if attack == "extra":
        data["private"] = "PRIVATE"
    elif attack == "nested_extra":
        data["first_handler_failure"]["private"] = "PRIVATE"
    elif attack == "bucket":
        data["elapsed_seconds_bucket"] = "PRIVATE"
    else:
        field = {
            "phase": "phase",
            "event": "untrusted_event_kind",
            "exception": "exception",
        }[attack]
        data["first_handler_failure"][field] = "PRIVATE"
    diag.emit_failure(
        "before_cleanup", NS(failure_diagnostic=lambda: data), None, ValueError()
    )
    raw = capsys.readouterr().out
    assert "PRIVATE" not in raw and "private" not in raw
    json.loads(raw.split(":", 1)[1])
