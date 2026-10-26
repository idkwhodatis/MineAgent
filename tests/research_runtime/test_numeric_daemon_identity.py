"""Bounded observer fault doubles; real persisted Docker gate stays mandatory."""

import copy
import os
import time
from types import SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor import numeric_daemon_identity as m

CID = "c" * 64
IMAGE = "sha256:" + "a" * 64
PID = 12345


def host_files():
    fields = ["S", "100"] + ["0"] * 17 + ["123456"]
    return {
        "stat": f"{PID} (python) " + " ".join(fields),
        "status": f"Pid:\t{PID}\nPPid:\t100\nState:\tS (sleeping)\nUid:\t0 0 0 0\nGid:\t0 0 0 0\nNSpid:\t{PID} 1\n",
        "cgroup": "0::/docker/" + CID + "\n",
    }


def client(monkeypatch, *, inspect_change=None, tops=None, host_change=None):
    files = host_files()
    reads = []

    def read(pid, name, maximum):
        assert pid == PID
        reads.append(name)
        if host_change is not None:
            return host_change(name, files[name], reads.count(name))
        return files[name]

    monkeypatch.setattr(m, "_read", read)
    monkeypatch.setattr(
        os,
        "readlink",
        lambda *_a, **_k: pytest.fail("protected namespace readlink must not be used"),
    )
    doc = {
        "Id": CID,
        "Image": IMAGE,
        "State": {"Pid": PID, "Status": "running", "Running": True},
    }
    inspections, commands = [], []

    def inspect(identity):
        assert identity == CID
        value = copy.deepcopy(doc)
        inspections.append(identity)
        if inspect_change is not None:
            inspect_change(value, len(inspections))
        return value

    def command(args):
        assert args == ["top", CID, "-eo", "pid,uid,ppid,pidns,comm"]
        commands.append(args)
        return 0, (
            tops[len(commands) - 1]
            if tops
            else f"PID UID PPID PIDNS COMMAND\n{PID} 0 100 4026532999 python\n".encode()
        )

    cli = SimpleNamespace(
        deadline=time.monotonic() + 8, _inspect=inspect, _command=command
    )
    return cli, inspections, commands


def test_real_daemon_inode_binds_host_pid1_without_namespace_readlink(monkeypatch):
    cli, inspections, commands = client(monkeypatch)
    ticks, inode, boot = m.observe_leader(cli, CID, IMAGE, PID)
    assert (ticks, inode) == (123456, 4026532999)
    assert boot.version is not None
    assert len(inspections) == 2 and len(commands) == 2


@pytest.mark.parametrize("field", ["Id", "Image", "Pid", "Running", "Status"])
@pytest.mark.parametrize("when", [1, 2])
def test_exact_container_identity_required_before_and_after(monkeypatch, field, when):
    def change(doc, count):
        if count == when:
            if field in {"Id", "Image"}:
                doc[field] = "bad"
            else:
                doc["State"][field] = {
                    "Pid": PID + 1,
                    "Running": False,
                    "Status": "exited",
                }[field]

    cli, _, _ = client(monkeypatch, inspect_change=change)
    with pytest.raises(ProcessError, match="kernel_identity"):
        m.observe_leader(cli, CID, IMAGE, PID)


@pytest.mark.parametrize(
    "change", ["ticks", "namespace", "nspid", "uid", "ppid", "cgroup", "zombie"]
)
def test_host_pid_reuse_and_cross_observation_drift_rejected(monkeypatch, change):
    def mutate(name, value, count):
        if count < 2:
            return value
        if change == "ticks" and name == "stat":
            return value.replace("123456", "654321")
        if change == "nspid" and name == "status":
            return value.replace(f"{PID} 1", f"{PID} 2")
        if change == "uid" and name == "status":
            return value.replace("Uid:\t0 0 0 0", "Uid:\t65533 65533 65533 65533")
        if change == "ppid" and name == "status":
            return value.replace("PPid:\t100", "PPid:\t101")
        if change == "cgroup" and name == "cgroup":
            return "0::/other\n"
        if change == "zombie" and name == "stat":
            return value.replace(") S", ") Z")
        return value

    first = f"PID UID PPID PIDNS COMMAND\n{PID} 0 100 4026532999 python\n".encode()
    tops = (
        [first, first.replace(b"4026532999", b"4026533000")]
        if change == "namespace"
        else None
    )
    cli, _, _ = client(monkeypatch, tops=tops, host_change=mutate)
    with pytest.raises(ProcessError, match="kernel_identity"):
        m.observe_leader(cli, CID, IMAGE, PID)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"PID UID PPID PIDNS COMMAND\n12345 0 100 0 python\n",
        b"PID UID PPID PIDNS COMMAND\n12346 0 100 42 python\n",
        b"PID UID PPID PIDNS COMMAND\n12345 65533 100 42 python\n",
        b"PID UID PPID PIDNS COMMAND\n12345 0 101 42 python\n",
        b"PID UID PPID PIDNS COMMAND\n12345 0 100 42 python\n12345 0 100 42 python\n",
    ],
)
def test_top_missing_duplicate_or_mismatched_pid_is_not_authority(monkeypatch, raw):
    cli, _, _ = client(monkeypatch, tops=[raw, raw])
    with pytest.raises(ProcessError, match="kernel_identity"):
        m.observe_leader(cli, CID, IMAGE, PID)


def test_original_deadline_never_renews_or_spawns_after_expiry(monkeypatch):
    cli, inspections, commands = client(monkeypatch)
    cli.deadline = time.monotonic() - 1
    with pytest.raises(ProcessError):
        m.observe_leader(cli, CID, IMAGE, PID)
    assert inspections == commands == []


def test_longer_unit_budget_is_preserved_but_observation_is_locally_capped(monkeypatch):
    cli, _, _ = client(monkeypatch)
    original = cli.deadline = time.monotonic() + 60
    before = time.monotonic()
    command = cli._command

    def bounded(args):
        assert before < cli.deadline <= time.monotonic() + m.OBSERVATION_SECONDS
        assert cli.deadline < original
        return command(args)

    cli._command = bounded
    assert m.observe_leader(cli, CID, IMAGE, PID)[:2] == (123456, 4026532999)
    assert cli.deadline == original


def test_observation_overrun_is_rejected_without_extending_unit_budget(monkeypatch):
    cli, _, commands = client(monkeypatch)
    original = cli.deadline = time.monotonic() + 60
    clock = [time.monotonic()]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    command = cli._command

    def overdue(args):
        answer = command(args)
        clock[0] += m.OBSERVATION_SECONDS + 1
        return answer

    cli._command = overdue
    with pytest.raises(ProcessError, match="kernel_identity"):
        m.observe_leader(cli, CID, IMAGE, PID)
    assert len(commands) == 1
    assert cli.deadline == original
