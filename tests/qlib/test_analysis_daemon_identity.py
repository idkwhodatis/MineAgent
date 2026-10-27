"""Bounded daemon observer doubles, never kernel/Docker acceptance."""


import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.qlib import daemon_identity as d


def test_fixed_transport_receives_absolute_deadline(monkeypatch):
    calls = []

    class Client:
        def _command(self, args):
            calls.append((self.deadline, args))
            return 0, b"PID UID PPID PIDNS COMMAND\n123 0 1 999 python\n"

    monkeypatch.setattr(d, "NumericDockerClient", Client)
    monkeypatch.setattr(d.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(d, "_inspect", lambda *_: None)
    host = ("S", 55, "/same", 1, (0, 0, 0, 0), (0, 0, 0, 0))
    monkeypatch.setattr(d, "_host_snapshot", lambda *_: host)
    ticks, namespace, _boot = d.observe_leader(
        "a" * 64, "sha256:" + "b" * 64, 123, 18.0
    )
    assert (ticks, namespace) == (55, 999)
    assert calls == [(18.0, ["top", "a" * 64, "-eo", d.TOP_FIELDS])]


@pytest.mark.parametrize("deadline", [9.0, 71.0, float("nan"), float("inf")])
def test_expired_unbounded_clock_rejected(monkeypatch, deadline):
    monkeypatch.setattr(d.time, "monotonic", lambda: 10.0)
    with pytest.raises(ProcessError):
        d.observe_leader("a" * 64, "sha256:" + "b" * 64, 123, deadline)
