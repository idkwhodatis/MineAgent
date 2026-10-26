"""Trusted bootstrap control-flow doubles only; never execute candidate source."""

import pytest

from packages.rdagent_adapter.numeric_profile import NUMERIC_CHILD_ENV
from runtimes.factor_execution import numeric_deadline as module


class ExecObserved(BaseException):
    pass


def patch_child(monkeypatch, *, bad_uid=False):
    actions = []
    for name in ("setsid", "setgroups", "setresgid", "setresuid", "dup2", "chdir"):
        monkeypatch.setattr(
            module.os, name, lambda *args, _name=name: actions.append((_name, args))
        )
    monkeypatch.setattr(
        module.os, "getresuid", lambda: (0, 0, 0) if bad_uid else (65533,) * 3
    )
    monkeypatch.setattr(module.os, "getresgid", lambda: (65533,) * 3)
    monkeypatch.setattr(module.os, "getgroups", list)
    monkeypatch.setattr(module.os, "open", lambda *args: 99)
    monkeypatch.setattr(
        module.resource, "setrlimit", lambda *args: actions.append(("limit", args))
    )
    for name in ("drop_capabilities", "prctl", "require_caps", "close_fds"):
        monkeypatch.setattr(
            module.lifecycle,
            name,
            lambda *args, _name=name: actions.append((_name, args)),
        )

    def execute(binary, argv, env):
        actions.append(("exec", (binary, argv, env)))
        raise ExecObserved

    monkeypatch.setattr(module.os, "execve", execute)
    return actions


@pytest.mark.parametrize("role", ["candidate", "normalizer"])
def test_numeric_exec_is_fixed_after_permanent_privilege_drop(monkeypatch, role):
    actions = patch_child(monkeypatch)
    monkeypatch.setenv("PROVIDER_SECRET", "must-not-reach-child")
    child = module.candidate_child if role == "candidate" else module.normalizer_child
    with pytest.raises(ExecObserved):
        child(object())
    names = [name for name, _ in actions]
    assert (
        names.index("setresuid")
        < names.index("drop_capabilities")
        < names.index("close_fds")
        < names.index("exec")
    )
    _, argv, env = actions[-1][1]
    assert argv[1:] == list(
        module.CANDIDATE_ARGS if role == "candidate" else module.NORMALIZER_ARGS
    )
    assert env == dict(NUMERIC_CHILD_ENV)
    assert (
        "PROVIDER_SECRET" not in env
        and "PYTHONPATH" not in env
        and "LD_PRELOAD" not in env
    )
    assert env["HDF5_PLUGIN_PRELOAD"] == "::" and env["OPENBLAS_NUM_THREADS"] == "1"


def test_numeric_child_does_not_exec_if_credential_drop_drifted(monkeypatch):
    actions = patch_child(monkeypatch, bad_uid=True)
    with pytest.raises(RuntimeError):
        module.candidate_child(object())
    assert all(name != "exec" for name, _ in actions)


def test_numeric_child_rejects_any_unregistered_command(monkeypatch):
    actions = patch_child(monkeypatch)
    with pytest.raises(RuntimeError):
        module._numeric_child(object(), ("-c", "arbitrary"))
    assert not actions


@pytest.mark.parametrize("role", ["candidate", "normalizer"])
def test_fixed_wrapper_entrypoint_does_not_suppress_lifecycle_failure(
    monkeypatch, role
):
    expected = (
        module.candidate_child if role == "candidate" else module.normalizer_child
    )

    def fail(callback, basename):
        assert callback is expected
        assert basename == ("result.h5" if role == "candidate" else "values.parquet")
        raise RuntimeError("bootstrap")

    seen = []
    monkeypatch.setattr(module, "_main", fail)
    monkeypatch.setattr(module.lifecycle, "fail_closed", seen.append)
    (module.run_candidate if role == "candidate" else module.run_normalizer)()
    assert len(seen) == 1 and isinstance(seen[0], RuntimeError)


@pytest.mark.parametrize("role", ["candidate", "normalizer"])
def test_numeric_pid1_has_one_child_no_observer_and_exact_export_binding(
    monkeypatch, role
):
    from types import SimpleNamespace

    actions = []
    monkeypatch.setattr(module.os, "getpid", lambda: 1)
    monkeypatch.setattr(module.os, "getresuid", lambda: (0, 0, 0))
    monkeypatch.setattr(module.os, "getresgid", lambda: (0, 0, 0))
    monkeypatch.setattr(module.sys, "argv", ["fixed", "8000"])
    monkeypatch.setattr(module.sys, "flags", SimpleNamespace(isolated=1))
    monkeypatch.setattr(module.time, "time_ns", lambda: 0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(module.ctypes, "CDLL", lambda *args, **kwargs: object())
    monkeypatch.setattr(module.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module.os, "fork", lambda: actions.append("fork") or 17)
    monkeypatch.setattr(module.os, "waitpid", lambda *args: (17, 0))
    for name in ("prctl", "require_caps", "drop_capabilities"):
        monkeypatch.setattr(module.lifecycle, name, lambda *args: None)
    monkeypatch.setattr(module.lifecycle, "helper", None)
    monkeypatch.setattr(module, "_export_helper", lambda *args: actions.append(args))

    def quiesce(expires, code):
        assert expires == 108.0 and code == 0
        module.lifecycle.helper("export", expires, code)
        raise ExecObserved

    monkeypatch.setattr(module.lifecycle, "quiesce_and_hold", quiesce)
    callback = (
        module.candidate_child if role == "candidate" else module.normalizer_child
    )
    basename = "result.h5" if role == "candidate" else "values.parquet"
    with pytest.raises(ExecObserved):
        module._main(callback, basename)
    assert actions == ["fork", (basename, "export", 108.0, 0)]


def test_numeric_main_rejects_cross_role_export_before_any_syscall():
    with pytest.raises(RuntimeError, match="unknown fixed numeric role"):
        module._main(module.candidate_child, "values.parquet")
