"""Pure/lifecycle-double checks; never execute protected wrapper on local host."""

import ast
import inspect
import json
import os
from pathlib import Path

import pytest

from runtimes.openevolve_operational import deadline as module
from runtimes.openevolve_operational import trusted_report as report


class Exit(BaseException):
    def __init__(self, code):
        self.code = code


def stop(code):
    raise Exit(code)


@pytest.mark.parametrize(
    "value,now,expected", [("600001", 1, 600.0), ("1001", 1, 1.0), ("2", 1, 0.001)]
)
def test_fixed_absolute_deadline(value, now, expected):
    assert module.remaining_seconds(value, now) == expected


@pytest.mark.parametrize(
    "value,now",
    [
        ("600002", 1),
        ("1", 1),
        ("0", 1),
        ("-1", 1),
        ("١", 1),
        ("1.1", 1),
        (" 2", 1),
        ("2", True),
        (2, 1),
        ("1" * 17, 1),
    ],
)
def test_deadline_invalid_or_excessive(value, now):
    with pytest.raises(ValueError):
        module.remaining_seconds(value, now)


def test_actual_own_proc_start_ticks_consistent():
    assert (
        module.child_start_ticks(os.getpid())
        == module.child_start_ticks(os.getpid())
        > 0
    )


@pytest.mark.parametrize(
    "event,code",
    [("started", None), ("quiescent", 0), ("quiescent", -9), ("quiescent", 125)],
)
def test_reports_exact_fixed_keys_and_bounded_frame(event, code):
    raw = report.report_bytes(event, 2, 1, code)
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1 and len(raw) <= 1024
    value = json.loads(raw)
    keys = {"schema", "event", "child_namespace_pid", "child_start_ticks"}
    if event == "quiescent":
        keys |= {"child_exit_code", "descendants_reaped"}
        assert value["descendants_reaped"] is True
    assert set(value) == keys
    assert value["schema"] == report.SCHEMA
    assert "stopped" not in value and "positive_stop" not in value


@pytest.mark.parametrize(
    "args",
    [
        ("started", 1, 1),
        ("started", True, 1),
        ("started", 2, 0),
        ("started", 2, True),
        ("started", 2, 1, 0),
        ("quiescent", 2, 1, None),
        ("quiescent", 2, 1, True),
        ("quiescent", 2, 1, 256),
        ("other", 2, 1),
    ],
)
def test_report_rejects_non_kernel_or_unbounded_metadata(args):
    with pytest.raises(ValueError):
        report.report_bytes(*args)


def test_report_single_write_no_retry(monkeypatch):
    writes = []
    monkeypatch.setattr(
        report.os, "write", lambda fd, data: writes.append((fd, data)) or len(data)
    )
    report.emit_report("started", 2, 3)
    assert writes == [(1, report.report_bytes("started", 2, 3))]
    monkeypatch.setattr(report.os, "write", lambda fd, data: 0)
    with pytest.raises(OSError):
        report.emit_report("started", 2, 3)


def test_quiescent_report_only_after_echild_and_original_deadline(monkeypatch):
    events = []
    clock = [0.0]
    waits = iter([(3, 0), (0, 0), ChildProcessError()])
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(
        module.os, "kill", lambda pid, sig: events.append(("kill", pid, sig))
    )

    def wait(pid, flags):
        events.append(("wait", pid, flags))
        result = next(waits)
        if isinstance(result, BaseException):
            events.append(("ECHILD",))
            raise result
        return result

    monkeypatch.setattr(module.os, "waitpid", wait)
    monkeypatch.setattr(
        module, "_fork_report", lambda *args: events.append(("report", *args))
    )
    monkeypatch.setattr(module.os, "_exit", stop)
    with pytest.raises(Exit) as exited:
        module.quiesce_and_hold(0.05, 2, 123, 0)
    assert exited.value.code == 124
    assert events[-1] == ("report", "quiescent", 2, 123, 0)
    assert events.index(("ECHILD",)) < len(events) - 1
    assert 0.05 <= clock[0] <= 0.06


def test_deadline_while_descendants_live_never_reports(monkeypatch):
    clock = [0.0]
    reports = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(module.os, "kill", lambda *args: None)
    monkeypatch.setattr(module.os, "waitpid", lambda *args: (0, 0))
    monkeypatch.setattr(module, "_fork_report", lambda *args: reports.append(args))
    monkeypatch.setattr(module.os, "_exit", stop)
    with pytest.raises(Exit) as exited:
        module.quiesce_and_hold(0.01, 2, 123, 0)
    assert exited.value.code == 124 and reports == []


def test_profile_fixed_entry_environment_and_input_documentation():
    assert module.PROFILE == "mineagent.openevolve-orchestrator.v1"
    assert module.CHILD_ARGS == (
        "-I",
        "-m",
        "runtimes.openevolve_operational.entrypoint",
    )
    env = dict(module.CHILD_ENV)
    assert len(env) == len(module.CHILD_ENV)
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        assert env[name] == "1"
    assert env["HDF5_PLUGIN_PRELOAD"] == "::"
    assert not any(
        name in env for name in ("PYTHONPATH", "PYTHONHOME", "HTTP_PROXY", "API_KEY")
    )
    assert "/bootstrap" in module.__doc__ and "/ipc" in module.__doc__


def test_pid1_has_no_output_or_shared_export_fallback():
    source = Path(module.__file__).read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "main",
            "quiesce_and_hold",
            "_fork_report",
        }:
            calls = [
                ast.unparse(call.func)
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            ]
            assert not set(calls) & {
                "print",
                "os.write",
                "os.read",
                "json.loads",
                "lifecycle.quiesce_and_hold",
                "lifecycle.helper",
                "lifecycle.fail_closed",
            }
    main = inspect.getsource(module.main)
    assert main.index("drop_capabilities(libc, lifecycle.KILL_MASK)") < main.index(
        '_fork_report("started"'
    )
    assert "os.waitpid(child, os.WNOHANG)" in main
    child = inspect.getsource(module.controller_child)
    assert child.index("os.dup2(null, fd)") < child.index("os.execve")
    assert "lifecycle.require_caps(0)" in child
    helper = inspect.getsource(module.report_helper)
    assert helper.index("lifecycle.require_caps(0)") < helper.index("emit_report(")
    assert "protected_output" not in source


def test_child_limits_do_not_raise_above_fixed_docker_hard_limits():
    tree = ast.parse(inspect.getsource(module.controller_child))
    limits = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "resource.setrlimit"
        ):
            limits[ast.unparse(node.args[0])] = ast.literal_eval(node.args[1])
    assert limits == {
        "resource.RLIMIT_CORE": (0, 0),
        "resource.RLIMIT_NOFILE": (256, 256),
        "resource.RLIMIT_FSIZE": (8388608, 8388608),
    }
