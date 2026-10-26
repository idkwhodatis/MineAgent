"""Pinned no-IPC PID1 observation without ptrace-protected namespace readlink.

The trusted local Docker daemon's fixed top command supplies the real namespace
inode. Host stat/status/NSpid/cgroup and before/after exact container inspection
independently bind it. No exec, shell, guessed inode, privilege change or fallback.
The caller's sanitized NumericDockerClient retains its original absolute budget.
"""

import math
import re
import time
from pathlib import Path
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError

TOP_FIELDS = "pid,uid,ppid,pidns,comm"
OBSERVATION_SECONDS = 5.0


def fail():
    raise ProcessError("numeric_process_kernel_identity")


def parse_top(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= 16384:
        fail()
    lines = raw.decode("ascii").splitlines()
    if not 2 <= len(lines) <= 17 or lines[0].split() != [
        "PID",
        "UID",
        "PPID",
        "PIDNS",
        "COMMAND",
    ]:
        fail()
    rows = {}
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 5 or any(
            re.fullmatch(r"[0-9]{1,20}", v) is None for v in fields[:4]
        ):
            fail()
        pid, uid, ppid, namespace = map(int, fields[:4])
        if pid <= 1 or pid in rows or namespace <= 0 or not 1 <= len(fields[4]) <= 64:
            fail()
        rows[pid] = (uid, ppid, namespace)
    return rows


def _read(pid, name, maximum):
    with (Path("/proc") / str(pid) / name).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not 1 <= len(raw) <= maximum:
        fail()
    return raw.decode("utf-8", errors="strict")


def host_snapshot(pid):
    raw = _read(pid, "stat", 16384)
    fields = raw.rsplit(")", 1)[1].split()
    if int(raw.split(" (", 1)[0]) != pid or fields[0] in {"Z", "X", "x"}:
        fail()
    ticks, ppid = int(fields[19]), int(fields[1])
    status = {}
    for line in _read(pid, "status", 65536).splitlines():
        key, value = line.split(":", 1)
        if key in status:
            fail()
        status[key] = value.strip()
    nspid = tuple(map(int, status["NSpid"].split()))
    uids = tuple(map(int, status["Uid"].split()))
    gids = tuple(map(int, status["Gid"].split()))
    cgroup = _read(pid, "cgroup", 4096)
    if (
        ticks <= 0
        or ppid <= 0
        or int(status["Pid"]) != pid
        or int(status["PPid"]) != ppid
        or status["State"].split()[0] in {"Z", "X", "x"}
        or len(nspid) < 2
        or nspid[0] != pid
        or nspid[-1] != 1
        or uids != (0, 0, 0, 0)
        or gids != (0, 0, 0, 0)
        or not cgroup.strip()
    ):
        fail()
    return ticks, ppid, nspid, uids, gids, cgroup


def observe_leader(client, container_id, image_id, pid):
    """Only the fixed supervisor calls this with its verified Docker identity."""
    original_deadline = client.deadline
    deadline = original_deadline
    if (
        type(container_id) is not str
        or re.fullmatch(r"[a-f0-9]{64}", container_id) is None
        or type(image_id) is not str
        or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None
        or type(pid) is not int
        or pid <= 1
        or type(deadline) not in (int, float)
        or not math.isfinite(deadline)
    ):
        fail()

    deadline = min(original_deadline, time.monotonic() + OBSERVATION_SECONDS)

    def current():
        if not 0 < deadline - time.monotonic() <= OBSERVATION_SECONDS:
            fail()

    def inspect_exact():
        current()
        value = client._inspect(container_id)
        if (
            type(value) is not dict
            or value.get("Id") != container_id
            or value.get("Image") != image_id
            or type(value.get("State")) is not dict
            or value["State"].get("Running") is not True
            or value["State"].get("Status") != "running"
            or type(value["State"].get("Pid")) is not int
            or value["State"]["Pid"] != pid
        ):
            fail()

    try:
        # A local observation cap may shorten, never extend, the unit budget.
        client.deadline = deadline
        inspect_exact()
        boot = UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        before = host_snapshot(pid)
        current()
        _, raw = client._command(["top", container_id, "-eo", TOP_FIELDS])
        first = parse_top(raw)
        middle = host_snapshot(pid)
        current()
        _, raw = client._command(["top", container_id, "-eo", TOP_FIELDS])
        second = parse_top(raw)
        after = host_snapshot(pid)
        inspect_exact()
        current()
        if (
            before != middle
            or before != after
            or pid not in first
            or pid not in second
            or first[pid] != second[pid]
            or first[pid][:2] != (0, after[1])
            or UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()) != boot
        ):
            fail()
        return after[0], second[pid][2], boot
    except (OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError):
        fail()
    finally:
        client.deadline = original_deadline
