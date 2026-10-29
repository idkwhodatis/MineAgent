"""Disabled RD-v2 protected PID1 successor; separate Docker acceptance required.

Fixed input layout is /input/bootstrap.json plus /ipc ticket/socket only. No
source/data/checkpoint mounts or host-writable mounts are admitted by this design.
Launcher must verify that closed inventory; this wrapper is not launch authority.
Audited d36cce6 lifecycle logic is reused for capability primitives, not its proof
observer/result.bin exporter. PID1 never writes/parses/waits on the output pipe.
"""

import ctypes
import os
import resource
import signal
import sys
import time

from packages.rdagent_adapter.controller_limits import (
    COMPOSED_CONTROLLER_PROFILE,
    LEGACY_CONTROLLER_PROFILE,
    SEEDED_CONTROLLER_PROFILE,
    controller_wall_milliseconds,
)
from runtimes.hostile_deadline import deadline as lifecycle

PROFILE = "mineagent.rdagent-orchestrator.v2"
MAX_WALL_MS = 60000
CHILD_UID = CHILD_GID = 65533
CHILD_ARGS = ("-I", "-m", "runtimes.rdagent_operational.entrypoint")
CHILD_ENV = (
    ("PATH", "/usr/local/bin:/usr/bin:/bin"),
    ("HOME", "/tmp"),
    ("LANG", "C.UTF-8"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("OMP_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
    ("VECLIB_MAXIMUM_THREADS", "1"),
    ("BLIS_NUM_THREADS", "1"),
    ("HDF5_PLUGIN_PRELOAD", "::"),
    ("HDF5_PLUGIN_PATH", ""),
)


def remaining_seconds(deadline_ms, now_ms, *, profile=LEGACY_CONTROLLER_PROFILE):
    if (
        type(deadline_ms) is not str
        or not deadline_ms.isascii()
        or not deadline_ms.isdecimal()
        or len(deadline_ms) > 16
        or type(now_ms) is not int
    ):
        raise ValueError("Exact absolute deadline required")
    remaining = int(deadline_ms) - now_ms
    if not 0 < remaining <= controller_wall_milliseconds(profile):
        raise ValueError("Expired or excessive controller deadline")
    return remaining / 1000


def controller_child(libc, *, profile=LEGACY_CONTROLLER_PROFILE, deadline_ms=None):
    os.setsid()
    os.setgroups([])
    os.setresgid(CHILD_GID, CHILD_GID, CHILD_GID)
    os.setresuid(CHILD_UID, CHILD_UID, CHILD_UID)
    lifecycle.drop_capabilities(libc)
    lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
    lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
    lifecycle.require_caps(0)
    if (
        os.getresuid() != (CHILD_UID,) * 3
        or os.getresgid() != (CHILD_GID,) * 3
        or os.getgroups()
    ):
        raise RuntimeError("Controller credentials did not drop permanently")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_FSIZE, (262144, 262144))
    null = os.open("/dev/null", os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    lifecycle.close_fds()
    os.chdir("/tmp")
    args = [sys.executable, *CHILD_ARGS]
    if profile != LEGACY_CONTROLLER_PROFILE:
        if (
            profile not in (COMPOSED_CONTROLLER_PROFILE, SEEDED_CONTROLLER_PROFILE)
            or deadline_ms is None
        ):
            raise ValueError("Exact composed controller arguments required")
        args.extend([profile, deadline_ms])
    os.execve(sys.executable, args, dict(CHILD_ENV))


def child_start_ticks(child):
    # Kernel metadata only, never application output. A zombie's stat remains
    # present until PID1 reaps it, so an early child exit cannot alias another PID.
    with open(f"/proc/{child}/stat", encoding="ascii") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise RuntimeError("Oversize kernel stat")
    value = int(raw.rsplit(")", 1)[1].split()[19])
    if value <= 0:
        raise RuntimeError("Invalid kernel start identity")
    return value


def report_helper(event, child, start, code=None):
    from runtimes.rdagent_operational.trusted_report import emit_report

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        lifecycle.drop_capabilities(libc)
        lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
        lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
        lifecycle.require_caps(0)
        if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
            raise RuntimeError("Trusted helper requires root identity")
        if libc.prctl(15, ctypes.c_char_p(b"ma-rd-report-v1"), 0, 0, 0) != 0:
            raise RuntimeError("Fixed report helper identity failed")
        lifecycle.close_fds()
        emit_report(event, child, start, code)
    except BaseException:  # noqa: BLE001 - output cannot defer PID1 deadline
        os._exit(125)
    os._exit(0)


def _fork_report(event, child, start, code=None):
    reporter = os.fork()
    if reporter == 0:
        report_helper(event, child, start, code)
    # Deliberately no wait for this helper, pipe read, or pipe write in PID1.


def quiesce_and_hold(expires, child, start, code):
    while True:
        if time.monotonic() >= expires:
            os._exit(124)
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            done, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break  # ECHILD before any quiescent report is allowed.
        if done == 0:
            time.sleep(0.005)
    if time.monotonic() >= expires:
        os._exit(124)
    _fork_report("quiescent", child, start, code)
    while time.monotonic() < expires:
        time.sleep(min(0.01, max(0, expires - time.monotonic())))
    os._exit(124)


def main():
    if os.getpid() != 1 or os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise RuntimeError("Requires private root PID1")
    if len(sys.argv) not in (2, 3) or not sys.flags.isolated:
        raise RuntimeError("Invalid fixed controller entrypoint")
    profile = LEGACY_CONTROLLER_PROFILE if len(sys.argv) == 2 else sys.argv[2]
    if len(sys.argv) == 3 and profile not in (
        COMPOSED_CONTROLLER_PROFILE,
        SEEDED_CONTROLLER_PROFILE,
    ):
        raise RuntimeError("Invalid explicit controller profile")
    expires = time.monotonic() + remaining_seconds(
        sys.argv[1], time.time_ns() // 1000000, profile=profile
    )
    libc = ctypes.CDLL(None, use_errno=True)
    lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
    lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
    lifecycle.require_caps(lifecycle.BOOTSTRAP_MASK)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: os._exit(124))
    child = os.fork()
    if child == 0:
        try:
            controller_child(libc, profile=profile, deadline_ms=sys.argv[1])
        except BaseException:  # noqa: BLE001 - no child-controlled output
            os._exit(125)
    start = child_start_ticks(child)
    if child_start_ticks(child) != start:
        raise RuntimeError("Child kernel identity changed")
    lifecycle.drop_capabilities(libc, lifecycle.KILL_MASK)
    lifecycle.require_caps(lifecycle.KILL_MASK)
    _fork_report("started", child, start)
    while True:
        if time.monotonic() >= expires:
            os._exit(124)
        done, result = os.waitpid(child, os.WNOHANG)
        if done:
            quiesce_and_hold(expires, child, start, os.waitstatus_to_exitcode(result))
        time.sleep(min(0.01, max(0, expires - time.monotonic())))


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - no PID1 output or fallback
        os._exit(125)
