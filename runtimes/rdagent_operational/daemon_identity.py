"""Container-bound read-only kernel observation through the trusted local daemon.

Docker top's daemon-side ps supplies the real PID namespace inode when host
readlink is denied by ptrace checks. No namespace value is inferred from NSpid,
process names or controller claims. Caller must supply immutable host-owned unit
context. No launch, exec, shell, capability change, or authority issuance occurs.
The shared CLI has a 10-second per-command ceiling; original-deadline checks reject
late results but do not claim to interrupt that CLI sooner.

Source: https://github.com/moby/moby/blob/master/daemon/top_unix.go
The accepted hostile-deadline no-reader proof uses this same fixed top inventory.
"""

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent_operational.peer import KernelIdentity
from runtimes.research_supervisor.supervisor import DockerSupervisor

MAX_TOP_BYTES = 16384
MAX_PROCESSES = 64
TOP_FIELDS = "pid,uid,ppid,pidns,comm"


def _fail():
    raise ProcessError("rd_controller_daemon_identity")


def parse_top(raw):
    """Strict daemon output only; names are bounded metadata, never authority."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_TOP_BYTES:
        _fail()
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        _fail()
    lines = text.splitlines()
    if not 2 <= len(lines) <= MAX_PROCESSES + 1 or lines[0].split() != [
        "PID",
        "UID",
        "PPID",
        "PIDNS",
        "COMMAND",
    ]:
        _fail()
    result = {}
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 5 or any(
            re.fullmatch(r"[0-9]{1,20}", v) is None for v in fields[:4]
        ):
            _fail()
        pid, uid, ppid, namespace = map(int, fields[:4])
        if (
            pid <= 0
            or pid in result
            or namespace <= 0
            or ppid < 0
            or not 1 <= len(fields[4]) <= 64
            or any(ord(c) < 33 or ord(c) > 126 for c in fields[4])
        ):
            _fail()
        result[pid] = (uid, ppid, namespace)
    return result


def _read(pid, name, maximum):
    with (Path("/proc") / str(pid) / name).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not 1 <= len(raw) <= maximum:
        _fail()
    return raw.decode("utf-8", errors="strict")


def _host_snapshot(pid):
    text = _read(pid, "stat", 16384)
    if int(text.split(" (", 1)[0]) != pid:
        _fail()
    fields = text.rsplit(")", 1)[1].split()
    start, stat_ppid = int(fields[19]), int(fields[1])
    if fields[0] in {"Z", "X", "x"} or start <= 0:
        _fail()
    status = {}
    for line in _read(pid, "status", 65536).splitlines():
        key, value = line.split(":", 1)
        if key in status:
            _fail()
        status[key] = value.strip()
    if int(status["Pid"]) != pid or status["State"].split()[0] in {"Z", "X", "x"}:
        _fail()
    uids, gids = (
        tuple(map(int, status["Uid"].split())),
        tuple(map(int, status["Gid"].split())),
    )
    ppid = int(status["PPid"])
    if (
        ppid != stat_ppid
        or len(uids) != 4
        or len(gids) != 4
        or any(v < 0 for v in (*uids, *gids))
    ):
        _fail()
    group = _read(pid, "cgroup", 4096)
    if not group.strip():
        _fail()
    return (pid, start, group, ppid, uids, gids)


def _inspect(cli, container_id, image_id, leader_pid):
    doc = cli._inspect(container_id)
    if (
        type(doc) is not dict
        or doc.get("Id") != container_id
        or doc.get("Image") != image_id
        or type(doc.get("State")) is not dict
        or doc["State"].get("Running") is not True
        or doc["State"].get("Status") != "running"
        or type(doc["State"].get("Pid")) is not int
        or doc["State"]["Pid"] != leader_pid
    ):
        _fail()


def _observe(*, container_id, image_id, leader_pid, pid, deadline):
    """Read exact requested member plus leader, rejecting any known unit/PID drift.

    Context must come from independently verified host launch records, never RPC
    request fields. Caller still validates leader/root and direct child/65533,
    compares immutable prior identities and authenticates SO_PEERCRED each event.
    """
    if (
        type(container_id) is not str
        or re.fullmatch(r"[a-f0-9]{64}", container_id) is None
        or type(image_id) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        or type(pid) is not int
        or pid <= 0
        or type(leader_pid) is not int
        or leader_pid <= 0
        or type(deadline) not in (int, float)
        or not math.isfinite(deadline)
    ):
        _fail()

    def current():
        if not 0 < deadline - time.monotonic() <= 60:
            _fail()

    try:
        cli = DockerSupervisor(image_id)
        current()
        _inspect(cli, container_id, image_id, leader_pid)
        current()
        targets = tuple(dict.fromkeys((leader_pid, pid)))
        before = {target: _host_snapshot(target) for target in targets}
        _, raw = cli._command(["top", container_id, "-eo", TOP_FIELDS])
        current()
        rows = parse_top(raw)
        after = {target: _host_snapshot(target) for target in targets}
        _inspect(cli, container_id, image_id, leader_pid)
        current()
        if before != after:
            _fail()
        for target, host in after.items():
            if target not in rows or rows[target][:2] != (host[4][1], host[3]):
                _fail()
        return {
            target: KernelIdentity(
                host_pid=target,
                start_ticks=host[1],
                cgroup=host[2],
                ppid=host[3],
                uids=host[4],
                gids=host[5],
                pid_namespace=f"pid:[{rows[target][2]}]",
            )
            for target, host in after.items()
        }
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        _fail()


@dataclass(frozen=True)
class DaemonIdentityContext:
    """Immutable host context, not serializable peer/execution authority."""

    container_id: str
    image_id: str
    leader_pid: int
    deadline: float

    def __post_init__(self):
        if (
            type(self.container_id) is not str
            or re.fullmatch(r"[a-f0-9]{64}", self.container_id) is None
            or type(self.image_id) is not str
            or re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_id) is None
            or type(self.leader_pid) is not int
            or self.leader_pid <= 0
            or type(self.deadline) not in (int, float)
            or not math.isfinite(self.deadline)
            or self.deadline <= 0
        ):
            _fail()

    def observe_pair(self, controller_pid=None):
        target = self.leader_pid if controller_pid is None else controller_pid
        values = _observe(
            container_id=self.container_id,
            image_id=self.image_id,
            leader_pid=self.leader_pid,
            pid=target,
            deadline=self.deadline,
        )
        return values[self.leader_pid], None if controller_pid is None else values[
            target
        ]

    def observe(self, pid):
        values = _observe(
            container_id=self.container_id,
            image_id=self.image_id,
            leader_pid=self.leader_pid,
            pid=pid,
            deadline=self.deadline,
        )
        return values[pid]


def observe_identity(*, container_id, image_id, leader_pid, pid, deadline):
    return DaemonIdentityContext(container_id, image_id, leader_pid, deadline).observe(
        pid
    )


# Same exact concrete type; descriptive batch-observer spelling is not authority.
DockerKernelObserver = DaemonIdentityContext
