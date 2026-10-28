"""Pure failure projection/owner doubles, no process, socket or provider calls."""

import json
import sys

import pytest
from test_ci_application_owner import FakeOwner, source

from script import ci_application_browser as gate
from script import ci_benchmark_failure as projection
from script import ci_process_owner as ownership

PRIVATE = "PRIVATE_TOKEN_https://private.invalid/path_ID"


def report(program="script.benchmark_v1_workloads"):
    return {
        "schema_version": projection.PROGRAMS[program],
        "status": "failed",
        "error_code": "concurrency_not_observed",
        (
            "concurrent_runs"
            if program.endswith("daily_reference")
            else "concurrent_pair"
        ): {},
    }


def arguments(program="script.benchmark_v1_workloads"):
    return [sys.executable, "-m", program]


@pytest.mark.parametrize("program", list(projection.PROGRAMS))
def test_exact_failed_projection_only(program):
    value = report(program)
    value["hardware"] = {"unknown_nested_private": PRIVATE}
    result = projection.decode_failure(arguments(program), json.dumps(value).encode())
    assert result["program"] == program
    assert result["last_reported_section"] in {"concurrent_pair", "concurrent_runs"}
    assert PRIVATE not in json.dumps(result)
    assert set(result) == {
        "program",
        "schema_version",
        "status",
        "error_code",
        "last_reported_section",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"extra": PRIVATE},
        {"status": "succeeded"},
        {"error_code": PRIVATE},
        {"error_code": True},
        {"error_code": 1},
        {"schema_version": "unknown"},
        {"concurrent_pair": PRIVATE},
        {"concurrent_pair": []},
    ],
)
def test_unknown_malformed_and_injected_fields_fail_closed(change):
    assert (
        projection.decode_failure(
            arguments(), json.dumps({**report(), **change}).encode()
        )
        is None
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"NaN",
        b'{"status":"failed","status":"succeeded"}',
        b"\xff",
        b" " * 1048577,
        b'{"error_code": Infinity}',
    ],
)
def test_invalid_json_duplicate_nonfinite_and_overlong_rejected(raw):
    assert projection.decode_failure(arguments(), raw) is None


@pytest.mark.parametrize(
    "args",
    [
        ["python", "-m", "script.benchmark_v1_workloads"],
        [sys.executable, "-m", "script.benchmark_v1"],
        [sys.executable, "-c", PRIVATE],
        [sys.executable],
    ],
)
def test_only_two_exact_module_launches_can_project(args):
    assert projection.decode_failure(args, json.dumps(report()).encode()) is None


@pytest.mark.parametrize("program", list(projection.PROGRAMS))
def test_nonzero_stays_failure_and_only_safe_metadata_is_printed(program, capsys):
    safe = projection.decode_failure(
        arguments(program), json.dumps(report(program)).encode()
    )

    class Owner:
        def run(self, *args, **kwargs):
            raise ownership.OwnedCommandFailed(safe)

    with pytest.raises(ownership.OwnedCommandFailed, match="Owned command failed"):
        gate.run_benchmark_command(Owner(), arguments(program), seconds=330)
    output = capsys.readouterr().out
    assert "BENCHMARK_FAILURE_V1:" in output
    assert "concurrency_not_observed" in output
    assert PRIVATE not in output


def test_unrecognized_failure_has_no_raw_attribute_or_projection(capsys):
    error = ownership.OwnedCommandFailed()
    assert not hasattr(error, "output") and not hasattr(error, "stdout")

    class Owner:
        def run(self, *args, **kwargs):
            raise error

    with pytest.raises(ownership.OwnedCommandFailed):
        gate.run_benchmark_command(Owner(), arguments(), seconds=330)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failure", ["benchmark", "service", "spawn"])
def test_boundary_failure_keeps_original_owned_cleanup(
    tmp_path, monkeypatch, capsys, failure
):
    instances = []

    class Owner(FakeOwner):
        def __init__(self, *_):
            super().__init__()
            self.interrupted = False
            self.cleanups = 0
            instances.append(self)

        def contain(self):
            pass

        def cleanup(self):
            self.cleanups += 1
            return True

        def spawn(self, *args, **kwargs):
            if failure == "spawn" and len(self.spawned) == 7:
                raise RuntimeError(PRIVATE)
            return super().spawn(*args, **kwargs)

        def run(self, args, **kwargs):
            if "script.benchmark_v1_workloads" in args:
                if failure == "service":
                    raise ownership.RequiredServiceExited(PRIVATE)
                safe = projection.decode_failure(
                    arguments(), json.dumps(report()).encode()
                )
                raise ownership.OwnedCommandFailed(safe)
            return super().run(args, **kwargs)

    monkeypatch.setattr(gate, "validate_source", lambda *_: "fixture")
    monkeypatch.setattr(gate.shutil, "which", lambda *a, **k: "/usr/bin/node")
    monkeypatch.setattr(gate, "ProcessOwner", Owner)
    monkeypatch.setattr(gate, "wait_ready", lambda _: None)
    monkeypatch.setattr(gate, "cleanup_containers", lambda *a: None)
    assert gate.run(source(tmp_path), tmp_path) == 1
    assert instances[0].cleanups == 2
    output = capsys.readouterr().out
    assert "OWNED_BROWSER_CLEANUP_V1:confirmed" in output
    assert PRIVATE not in output
    assert not (tmp_path / "var/ci-evidence/m6-workload-performance.json").exists()


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN"])
def test_nonfinite_numeric_tokens_anywhere_reject_projection(number):
    raw = (
        json.dumps(report()).removesuffix("}")
        + ',"hardware":{"private":'
        + number
        + "}}"
    )
    assert projection.decode_failure(arguments(), raw.encode()) is None


def test_deep_private_container_is_bounded_before_json_parse():
    raw = (
        json.dumps(report()).removesuffix("}")
        + ',"hardware":{"private":'
        + "[" * 40
        + "0"
        + "]" * 40
        + "}}"
    )
    assert projection.decode_failure(arguments(), raw.encode()) is None


@pytest.mark.parametrize(
    "payload", [report(), {"private": PRIVATE}, {**report(), "status": "succeeded"}]
)
def test_real_owner_nonzero_branch_retains_only_projection(
    monkeypatch, payload, capsys
):
    from types import SimpleNamespace

    from test_ci_application_owner import fake_clock, owner

    value = owner()
    fake_clock(monkeypatch)
    monkeypatch.setattr(value, "check", lambda: None)
    closed = []
    stream = SimpleNamespace(fileno=lambda: 42, close=lambda: closed.append(True))
    child = ownership.OwnedProcess(
        SimpleNamespace(pid=201, returncode=1, stdout=stream), False
    )
    monkeypatch.setattr(value, "spawn", lambda *a, **k: child)
    monkeypatch.setattr(ownership.os, "set_blocking", lambda *a: None)
    chunks = iter([json.dumps(payload).encode(), b""])
    monkeypatch.setattr(ownership.os, "read", lambda *a: next(chunks))

    class Selector:
        def __init__(self):
            self.mapping = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def register(self, *args):
            self.mapping[42] = True

        def unregister(self, *_):
            self.mapping.clear()

        def get_map(self):
            return self.mapping

        def select(self, *_):
            return [(SimpleNamespace(fileobj=stream), None)]

    monkeypatch.setattr(ownership.selectors, "DefaultSelector", Selector)
    with pytest.raises(ownership.OwnedCommandFailed) as error:
        value.run(arguments(), cwd="/tmp", env={}, seconds=330, maximum=1048576)
    assert closed == [True]
    assert str(error.value) == "Owned command failed"
    assert PRIVATE not in repr(vars(error.value))
    assert not hasattr(error.value, "stdout") and not hasattr(error.value, "output")
    assert capsys.readouterr().out == ""
    if payload == report():
        assert error.value.diagnostic["error_code"] == "concurrency_not_observed"
    else:
        assert error.value.diagnostic is None


@pytest.mark.parametrize(
    "change",
    [
        {"extra": PRIVATE},
        {"error_code": PRIVATE},
        {"status": "succeeded"},
        {"program": "script.benchmark_v1_daily_reference"},
    ],
)
def test_gate_revalidates_closed_exception_metadata(change, capsys):
    safe = projection.decode_failure(arguments(), json.dumps(report()).encode())

    class Owner:
        def run(self, *args, **kwargs):
            raise ownership.OwnedCommandFailed({**safe, **change})

    with pytest.raises(ownership.OwnedCommandFailed):
        gate.run_benchmark_command(Owner(), arguments(), seconds=330)
    assert capsys.readouterr().out == ""
