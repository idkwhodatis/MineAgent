"""Diagnostic vocabulary cannot expose exception payloads or spoofed tracebacks."""

import json
from types import FunctionType, TracebackType

import pytest

from backend.app.operations_backup import BackupError
from backend.app.rdagent_backup import _require
from tests.integration.cold_failure_diagnostic import (
    diagnose_cold_failure,
    failure_projection,
)


@diagnose_cold_failure
def fail():
    try:
        _require(False, "SECRET source user-id token")
    except ValueError:
        raise BackupError("SECRET outer") from None


def test_hidden_context_keeps_trusted_location_without_contents(capsys):
    with pytest.raises(BackupError) as caught:
        fail()
    output = capsys.readouterr().out
    assert "SECRET" not in output
    value = json.loads(output.removeprefix("COLD_FAILURE_LOCATION:"))
    assert [row["type"] for row in value["chain"]] == ["BackupError", "ValueError"]
    locations = value["chain"][1]["frames"]
    assert locations == [
        {
            "module": "backend.app.rdagent_backup",
            "function": "_require",
            "line": _require.__code__.co_firstlineno + 2,
        }
    ]
    assert caught.value.__suppress_context__ is True
    assert str(caught.value) == "SECRET outer"


def test_fake_module_function_filename_and_class_name_never_reported():
    def evil():
        raise ValueError("SECRET input")

    forged = FunctionType(
        evil.__code__.replace(
            co_filename=_require.__code__.co_filename,
            co_name="_require",
            co_qualname="_require",
        ),
        {"__name__": "backend.app.rdagent_backup"},
    )
    try:
        forged()
    except ValueError as error:
        assert failure_projection(error)["chain"] == [
            {"type": "ValueError", "frames": []}
        ]
    unknown = type("SECRET-class", (ValueError,), {})("SECRET args")
    assert failure_projection(unknown)["chain"] == [{"type": "other", "frames": []}]


def test_unregistered_code_clone_and_invalid_line_fail_closed():
    cloned = FunctionType(_require.__code__.replace(), _require.__globals__)
    try:
        cloned(False, "SECRET")
    except ValueError as error:
        assert failure_projection(error)["chain"][0]["frames"] == []
    try:
        _require(False, "SECRET")
    except ValueError as error:
        trace = error.__traceback__.tb_next
        error.__traceback__ = TracebackType(
            None, trace.tb_frame, trace.tb_lasti, 999999999
        )
        assert failure_projection(error)["chain"][0]["frames"] == []


def test_cycles_and_deep_context_are_bounded():
    error = ValueError("SECRET")
    error.__context__ = error
    assert len(failure_projection(error)["chain"]) == 1
    for _ in range(20):
        outer = ValueError("SECRET")
        outer.__context__ = error
        error = outer
    assert len(failure_projection(error)["chain"]) == 8


def test_projection_failure_does_not_replace_original(monkeypatch):
    from tests.integration import cold_failure_diagnostic as module

    original = BackupError("SECRET")

    def broken(*_):
        raise ValueError("SECRET diagnostics")

    monkeypatch.setattr(module, "failure_projection", broken)

    @diagnose_cold_failure
    def function():
        raise original

    with pytest.raises(BackupError) as caught:
        function()
    assert caught.value is original


def test_success_is_not_changed_or_logged(capsys):
    assert diagnose_cold_failure(lambda value: value)(42) == 42
    assert capsys.readouterr().out == ""


def test_exception_descriptors_cannot_inject_fake_trace_or_execute(capsys):
    class SecretLine(str):
        def __hash__(self):
            return hash(_require.__code__.co_firstlineno + 2)

        def __eq__(self, _other):
            return True

    class FakeCodeFrame:
        f_code = _require.__code__

    class FakeTrace:
        tb_frame = FakeCodeFrame()
        tb_lineno = SecretLine("SECRET-LINE-PAYLOAD")
        tb_next = None

    touched = []

    class Malicious(BackupError):
        def __getattribute__(self, name):
            touched.append("getattribute")
            return super().__getattribute__(name)

        @property
        def __traceback__(self):
            touched.append("trace")
            return FakeTrace()

        @property
        def __context__(self):
            touched.append("context")

    error = Malicious("SECRET args")
    result = failure_projection(error)
    assert touched == []
    assert "SECRET" not in json.dumps(result)
    assert result["chain"] == [{"type": "other", "frames": []}]


def test_exception_metaclass_hooks_are_never_executed():
    touched = []

    class Meta(type):
        def __hash__(cls):
            touched.append("hash")
            return hash(ValueError)

        def __eq__(cls, other):
            touched.append("eq")
            return True

    class Malicious(ValueError, metaclass=Meta):
        pass

    result = failure_projection(Malicious("SECRET"))
    assert touched == []
    assert result["chain"] == [{"type": "other", "frames": []}]


def test_nonexception_class_property_is_not_inspected():
    touched = []

    class Fake:
        @property
        def __class__(self):
            touched.append("class")
            return BaseException

    assert failure_projection(Fake())["chain"] == []
    assert touched == []


def test_hidden_native_context_bypasses_all_subclass_hooks():
    touched = []

    class Malicious(BackupError):
        def __getattribute__(self, name):
            touched.append("getattribute")
            raise AssertionError("SECRET")

        @property
        def __context__(self):
            touched.append("context")
            raise AssertionError("SECRET")

        @property
        def __traceback__(self):
            touched.append("traceback")
            raise AssertionError("SECRET")

    error = Malicious("SECRET")
    BaseException.__dict__["__context__"].__set__(error, ValueError("SECRET"))
    assert failure_projection(error)["chain"] == [
        {"type": "other", "frames": []},
        {"type": "ValueError", "frames": []},
    ]
    assert touched == []
