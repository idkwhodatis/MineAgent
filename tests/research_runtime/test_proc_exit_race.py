"""Procfs exit races prove absence; inaccessible or corrupt evidence does not."""

import errno
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest


@pytest.fixture(
    params=[
        "runtimes.openevolve_operational.supervisor",
        "runtimes.rdagent_operational.supervisor",
        "runtimes.openevolve_structured_host.supervisor",
        "runtimes.research_supervisor.numeric_supervisor",
        "runtimes.research_supervisor.analysis_supervisor",
        "runtimes.research_supervisor.openevolve_native_supervisor",
        "runtimes.research_supervisor.structured_factor_supervisor",
    ]
)
def probe(request, monkeypatch):
    module = import_module(request.param)
    boot_id = UUID(int=1)
    identity = SimpleNamespace(
        host_pid=12345,
        start_ticks=9876,
        init_pid=12345,
        init_start_ticks=9876,
        boot_id=boot_id,
    )
    controller = hasattr(module, "_kernel_gone")
    if controller:
        check = module._kernel_gone
    else:
        monkeypatch.setattr(module, "_boot_id", lambda: boot_id)
        check = module.kernel_identity_absent
    return check, identity, controller


@pytest.mark.parametrize("error_number", [errno.ENOENT, errno.ESRCH])
def test_proc_exit_errors_confirm_absence(probe, monkeypatch, error_number):
    check, identity, _ = probe

    def read(path, *args, **kwargs):
        assert path == Path("/proc/12345/stat")
        raise OSError(error_number, "process exited")

    monkeypatch.setattr(Path, "read_text", read)
    assert check(identity) is True


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EPERM, errno.EIO])
def test_proc_io_errors_do_not_confirm_absence(probe, monkeypatch, error_number):
    check, identity, _ = probe
    error = OSError(error_number, "unavailable evidence")

    def read(path, *args, **kwargs):
        assert path == Path("/proc/12345/stat")
        raise error

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(OSError) as raised:
        check(identity)
    assert raised.value is error


@pytest.mark.parametrize("state", ["R", "S", "Z", "X", "x"])
@pytest.mark.parametrize("reused", [False, True])
def test_proc_identity_and_zombie_policy_unchanged(probe, monkeypatch, state, reused):
    check, identity, controller = probe
    fields = [state, *("0" for _ in range(18)), str(9876 + reused)]
    text = "12345 (a name with ) parentheses) " + " ".join(fields)
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_kw: text)
    assert check(identity) is (reused or (controller and state in {"Z", "X", "x"}))


@pytest.mark.parametrize("text", ["", "12345 (name) S", "12345 (name) " + "x " * 20])
def test_malformed_proc_evidence_does_not_confirm_absence(probe, monkeypatch, text):
    check, identity, _ = probe
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_kw: text)
    with pytest.raises((IndexError, ValueError)):
        check(identity)
