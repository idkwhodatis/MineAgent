"""Trusted PID1; never import candidate modules or consume candidate output.

Installed at a fixed readonly path, launched only in a private PID namespace.
No application launcher currently admits this disabled profile.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import signal
import sys
import time

from runtimes.hostile_deadline.contract import (
    CANDIDATE_GID,
    CANDIDATE_UID,
    remaining_seconds,
)

PR_SET_DUMPABLE = 4
PR_SET_NO_NEW_PRIVS = 38
LINUX_CAPABILITY_VERSION_3 = 0x20080522
KILL_MASK = 1 << 5
BOOTSTRAP_MASK = KILL_MASK | (1 << 6) | (1 << 7)  # No SETPCAP/SYS_ADMIN.


class CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def prctl(libc, option: int, value: int) -> None:
    if libc.prctl(option, value, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl failed")


def drop_capabilities(libc, retained: int = 0) -> None:
    header = CapHeader(LINUX_CAPABILITY_VERSION_3, 0)
    data = (CapData * 2)()
    data[0].effective = data[0].permitted = retained
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise OSError(ctypes.get_errno(), "capset failed")


def parse_status(lines) -> dict[str, str]:
    # Split before stripping: valid empty fields such as Groups:\t\n must
    # retain their separator. Required capability/privilege checks remain strict.
    return {
        key: value.rstrip()
        for line in lines
        if ":\t" in line
        for key, value in [line.split(":\t", 1)]
    }


def status() -> dict[str, str]:
    with open("/proc/self/status", encoding="ascii") as stream:
        return parse_status(stream)


def require_caps(expected: int) -> None:
    observed = status()
    if any(int(observed[key], 16) != expected for key in ("CapEff", "CapPrm")):
        raise RuntimeError("unexpected active capabilities")
    if any(int(observed[key], 16) != 0 for key in ("CapInh", "CapAmb")):
        raise RuntimeError("inherited or ambient capability")
    if (
        int(observed["CapBnd"], 16) != BOOTSTRAP_MASK
        or observed["NoNewPrivs"] != "1"
        or observed["Seccomp"] != "2"
    ):
        raise RuntimeError("unexpected bounding set or privilege policy")


def close_fds() -> None:
    # Enumerating trusted procfs before exec also closes inherited descriptors
    # above a subsequently lowered RLIMIT_NOFILE. No credentials/IPC are inherited.
    for name in os.listdir("/proc/self/fd"):
        descriptor = int(name)
        if descriptor > 2:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise


def candidate(libc) -> None:
    os.setsid()
    os.setgroups([])
    os.setresgid(CANDIDATE_GID, CANDIDATE_GID, CANDIDATE_GID)
    os.setresuid(CANDIDATE_UID, CANDIDATE_UID, CANDIDATE_UID)
    drop_capabilities(libc)
    prctl(libc, PR_SET_NO_NEW_PRIVS, 1)
    prctl(libc, PR_SET_DUMPABLE, 0)
    require_caps(0)
    if (
        os.getresuid() != (CANDIDATE_UID,) * 3
        or os.getresgid() != (CANDIDATE_GID,) * 3
        or os.getgroups()
    ):
        raise RuntimeError("candidate credentials did not drop permanently")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    null = os.open("/dev/null", os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    close_fds()
    os.chdir("/tmp")
    os.execve(
        sys.executable,
        [sys.executable, "-I", "/input/candidate.py"],
        {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"},
    )


def helper(role: str, expires: float, child_exit_code: int = 0) -> None:
    from runtimes.hostile_deadline.protected_output import export_output, observe_marker

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        drop_capabilities(libc)
        prctl(libc, PR_SET_DUMPABLE, 0)
        prctl(libc, PR_SET_NO_NEW_PRIVS, 1)
        require_caps(0)
        name = b"ma-export-v2" if role == "export" else b"ma-observe-v2"
        if libc.prctl(15, ctypes.c_char_p(name), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "fixed helper identity failed")
        close_fds()
        if role == "export":
            export_output(child_exit_code)
        else:
            observe_marker(expires)
    except BaseException:  # noqa: BLE001 - helper failure never blocks PID1
        os._exit(125)
    os._exit(0)


def quiesce_and_hold(expires: float, child_exit_code: int) -> None:
    # PID1 adopts orphaned descendants. kill(-1) excludes the caller/PID1 and is
    # limited by this private PID namespace. Repeat to close fork/delivery races.
    # No host docker-exec/namespace injection is permitted during this profile.
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
            break  # ECHILD: no surviving descendants can create new descendants.
        if done == 0:
            time.sleep(0.005)
    if time.monotonic() >= expires:
        os._exit(124)
    exporter = os.fork()
    if exporter == 0:
        helper("export", expires, child_exit_code)
    # Never wait for or write on the exporter pipe. A stalled/killed reader cannot
    # extend this deadline; PID1 exit kills the exporter too.
    while time.monotonic() < expires:
        time.sleep(min(0.01, max(0, expires - time.monotonic())))
    os._exit(124)


def fail_closed(error: BaseException) -> None:
    """One bounded, nonblocking trusted-bootstrap diagnostic, then exit.

    No exception text, candidate data, retry or flush; a full/closed log pipe
    cannot delay namespace death. Candidate stdio is /dev/null before exec.
    """
    try:
        frame = error.__traceback__
        location = "unknown"
        for _ in range(16):
            if frame is None:
                break
            name = frame.tb_frame.f_code.co_name
            if name in {
                "main",
                "candidate",
                "require_caps",
                "drop_capabilities",
                "prctl",
                "status",
                "close_fds",
                "quiesce_and_hold",
            }:
                location = name + ":" + str(frame.tb_lineno)
            frame = frame.tb_next
        payload = (
            json.dumps(
                {
                    "schema": "mineagent.trusted-bootstrap-failure.v1",
                    "location": location,
                    "errno": error.errno if isinstance(error, OSError) else None,
                },
                separators=(",", ":"),
            ).encode("ascii")
            + b"\n"
        )
        os.set_blocking(2, False)
        os.write(2, payload[:256])
    except BaseException:  # noqa: BLE001, S110 - diagnostics cannot defer termination
        pass
    finally:
        os._exit(125)


def main() -> None:
    if os.getpid() != 1 or os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise RuntimeError("requires root PID1 in its own container PID namespace")
    if len(sys.argv) != 2 or not sys.flags.isolated:
        raise RuntimeError("invalid fixed entrypoint")
    remaining = remaining_seconds(sys.argv[1], time.time_ns() // 1_000_000)
    expires = time.monotonic() + remaining
    libc = ctypes.CDLL(None, use_errno=True)
    prctl(libc, PR_SET_DUMPABLE, 0)
    prctl(libc, PR_SET_NO_NEW_PRIVS, 1)
    require_caps(BOOTSTRAP_MASK)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Stop/interrupt exits PID1 rather than waiting on a potentially hostile tree.
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: os._exit(124))
    observer = os.fork()
    if observer == 0:
        helper("observe", expires)
    child = os.fork()
    if child == 0:
        try:
            candidate(libc)
        except BaseException as error:  # noqa: BLE001 - fail-closed bootstrap
            fail_closed(error)
    drop_capabilities(libc, KILL_MASK)
    require_caps(KILL_MASK)
    while True:
        # No output writes: a blocked log consumer cannot delay namespace death.
        if time.monotonic() >= expires:
            os._exit(124)
        done, result = os.waitpid(child, os.WNOHANG)
        if done:
            code = os.waitstatus_to_exitcode(result)
            quiesce_and_hold(expires, code)
        time.sleep(min(0.01, max(0, expires - time.monotonic())))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:  # noqa: BLE001 - fail-closed bootstrap
        # Any trusted-wrapper error kills the complete namespace; never fall back
        # to direct candidate execution or leave descendants running.
        fail_closed(error)
