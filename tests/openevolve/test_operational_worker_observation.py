"""Fixed daemon/host observation doubles; no local Docker authority claim."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.openevolve_adapter.placement_contracts import WorkerPlacementObservation
from runtimes.openevolve_operational import worker_observation as m
from runtimes.openevolve_operational.peer import ControllerBinding
from runtimes.rdagent_operational.peer import KernelIdentity


@pytest.fixture
def case(monkeypatch):
    leader = KernelIdentity(100, 10, "pid:[42]", "0::/fixed", 1, (0,) * 4, (0,) * 4)
    controller = KernelIdentity(
        101, 11, "pid:[42]", "0::/fixed", 100, (65533,) * 4, (65533,) * 4
    )
    worker = KernelIdentity(
        102, 12, "pid:[42]", "0::/fixed", 101, (65533,) * 4, (65533,) * 4
    )
    binding = ControllerBinding("a" * 64, "b" * 64, leader, controller)
    context = m.DaemonIdentityContext("a" * 64, "sha256:" + "c" * 64, 100, 600)
    pair = Mock(return_value=(leader, controller))
    observe = Mock(return_value=worker)
    monkeypatch.setattr(
        m.DaemonIdentityContext, "observe_pair", lambda self, pid: pair(pid)
    )
    monkeypatch.setattr(
        m.DaemonIdentityContext, "observe", lambda self, pid: observe(pid)
    )
    cli = NS(
        _command=Mock(
            return_value=(
                0,
                b"PID UID PPID PIDNS COMMAND\n100 0 1 42 python\n101 65533 100 42 python\n102 65533 101 42 python\n103 65533 101 42 python\n",
            )
        )
    )
    monkeypatch.setattr(m, "DockerSupervisor", lambda *_: cli)
    inspect = Mock()
    monkeypatch.setattr(m, "_inspect", inspect)
    statuses = {101: (100, (101, 2)), 102: (101, (102, 3)), 103: (101, (103, 4))}
    monkeypatch.setattr(m, "_status", lambda pid: statuses[pid])
    monkeypatch.setattr(m.time, "monotonic", lambda: 1)
    placement = WorkerPlacementObservation(
        schema_version="mineagent.openevolve-worker-placement.v1",
        epoch_id=uuid4(),
        intent_id=uuid4(),
        controller_namespace_pid=2,
        worker_namespace_pid=3,
        worker_start_ticks=12,
    )
    return NS(
        leader=leader,
        controller=controller,
        worker=worker,
        binding=binding,
        context=context,
        placement=placement,
        pair=pair,
        observe=observe,
        cli=cli,
        inspect=inspect,
        statuses=statuses,
    )


def test_exact_worker_not_resource_tracker(case):
    assert m.observe_worker(case.context, case.binding, case.placement) == case.worker
    case.observe.assert_called_once_with(102)
    assert case.pair.call_count == 2 and case.inspect.call_count == 2
    case.cli._command.assert_called_once_with(
        ["top", "a" * 64, "-eo", "pid,uid,ppid,pidns,comm"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_ticks", 13),
        ("ppid", 100),
        ("uids", (0,) * 4),
        ("gids", (0,) * 4),
        ("pid_namespace", "pid:[43]"),
        ("cgroup", "0::/other"),
    ],
)
def test_observed_worker_cannot_drift(case, field, value):
    case.observe.return_value = replace(case.worker, **{field: value})
    with pytest.raises(ValueError):
        m.observe_worker(case.context, case.binding, case.placement)


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "duplicate",
        "controller_namespace",
        "parent",
        "changed_controller",
        "late",
        "foreign_container",
    ],
)
def test_fail_closed_mapping(case, monkeypatch, mode):
    if mode == "missing":
        case.statuses[102] = (101, (102, 9))
    if mode == "duplicate":
        case.statuses[103] = (101, (103, 3))
    if mode == "controller_namespace":
        case.statuses[101] = (100, (101, 7))
    if mode == "parent":
        case.statuses[102] = (999, (102, 3))
    if mode == "changed_controller":
        case.pair.side_effect = [
            (case.leader, case.controller),
            (case.leader, replace(case.controller, start_ticks=99)),
        ]
    if mode == "late":
        monkeypatch.setattr(m.time, "monotonic", lambda: 601)
    if mode == "foreign_container":
        case.context = replace(case.context, container_id="f" * 64)
    with pytest.raises(ValueError):
        m.observe_worker(case.context, case.binding, case.placement)


def test_namespace_chain_reread_change(case, monkeypatch):
    calls = 0

    def status(pid):
        nonlocal calls
        if pid == 102:
            calls += 1
            return (101, (102, 3 if calls == 1 else 4))
        return case.statuses[pid]

    monkeypatch.setattr(m, "_status", status)
    with pytest.raises(ValueError):
        m.observe_worker(case.context, case.binding, case.placement)
