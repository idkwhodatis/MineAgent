"""Disabled numeric successor: fixed child ABI, not the stdlib proof profile.

Reuse the trusted PID1 lifecycle implementation without changing its historical
entrypoint. This image/profile needs separate hostile/native Docker acceptance.
No numeric/native library is imported by PID1 or before credential separation.
"""

import ctypes
import os
import resource
import signal
import sys
import time

from runtimes.hostile_deadline import deadline as lifecycle

NUMERIC_ENV = (
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
CANDIDATE_ARGS = ("-I", "/input/source.py")
NORMALIZER_ARGS = ("-I", "-m", "runtimes.factor_normalizer.entrypoint")


def _numeric_child(libc, command):
    if type(command) is not tuple or command not in (CANDIDATE_ARGS, NORMALIZER_ARGS):
        raise RuntimeError("unknown fixed numeric child")
    os.setsid()
    os.setgroups([])
    os.setresgid(
        lifecycle.CANDIDATE_GID, lifecycle.CANDIDATE_GID, lifecycle.CANDIDATE_GID
    )
    os.setresuid(
        lifecycle.CANDIDATE_UID, lifecycle.CANDIDATE_UID, lifecycle.CANDIDATE_UID
    )
    lifecycle.drop_capabilities(libc)
    lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
    lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
    lifecycle.require_caps(0)
    if (
        os.getresuid() != (lifecycle.CANDIDATE_UID,) * 3
        or os.getresgid() != (lifecycle.CANDIDATE_GID,) * 3
        or os.getgroups()
    ):
        raise RuntimeError("numeric child credentials did not drop permanently")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    null = os.open("/dev/null", os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null, descriptor)
    lifecycle.close_fds()
    os.chdir("/tmp")
    os.execve(sys.executable, [sys.executable, *command], dict(NUMERIC_ENV))


def candidate_child(libc):
    _numeric_child(libc, CANDIDATE_ARGS)


def normalizer_child(libc):
    _numeric_child(libc, NORMALIZER_ARGS)


def _export_helper(name, role, expires, child_exit_code=0):
    """Only a protected post-ECHILD child invokes the numeric exporter."""
    from runtimes.factor_execution.protected_output import export_output

    try:
        if role != "export" or name not in ("result.h5", "values.parquet"):
            raise RuntimeError("numeric profile has no observer or generic export")
        libc = ctypes.CDLL(None, use_errno=True)
        lifecycle.drop_capabilities(libc)
        lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
        lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
        lifecycle.require_caps(0)
        if libc.prctl(15, ctypes.c_char_p(b"ma-export-v2"), 0, 0, 0) != 0:
            raise RuntimeError("numeric exporter identity failed")
        lifecycle.close_fds()
        export_output(name, child_exit_code)
    except BaseException:  # noqa: BLE001 - helper cannot delay PID1
        os._exit(125)
    os._exit(0)


def _main(child_callback, output_name):
    # Copy only the accepted d36cce6 PID1 orchestration, omitting its proof-only
    # observer fork. Shared quiesce still establishes ECHILD and enforces the
    # unchanged original deadline; a fixed numeric helper replaces its exporter.
    if (child_callback, output_name) not in (
        (candidate_child, "result.h5"),
        (normalizer_child, "values.parquet"),
    ):
        raise RuntimeError("unknown fixed numeric role")
    if os.getpid() != 1 or os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise RuntimeError("requires private root PID1")
    if len(sys.argv) != 2 or not sys.flags.isolated:
        raise RuntimeError("invalid fixed entrypoint")
    remaining = lifecycle.remaining_seconds(sys.argv[1], time.time_ns() // 1_000_000)
    expires = time.monotonic() + remaining
    libc = ctypes.CDLL(None, use_errno=True)
    lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
    lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
    lifecycle.require_caps(lifecycle.BOOTSTRAP_MASK)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: os._exit(124))
    # Immutable trusted callback installed before fork; never request/env selected.
    lifecycle.helper = lambda role, deadline, code=0: _export_helper(
        output_name, role, deadline, code
    )
    child = os.fork()
    if child == 0:
        try:
            child_callback(libc)
        except BaseException as error:  # noqa: BLE001 - fail closed
            lifecycle.fail_closed(error)
    lifecycle.drop_capabilities(libc, lifecycle.KILL_MASK)
    lifecycle.require_caps(lifecycle.KILL_MASK)
    while True:
        if time.monotonic() >= expires:
            os._exit(124)
        done, result = os.waitpid(child, os.WNOHANG)
        if done:
            lifecycle.quiesce_and_hold(expires, os.waitstatus_to_exitcode(result))
        time.sleep(min(0.01, max(0, expires - time.monotonic())))


def run_candidate():
    try:
        _main(candidate_child, "result.h5")
    except BaseException as error:  # noqa: BLE001 - PID1 must fail closed
        lifecycle.fail_closed(error)


def run_normalizer():
    try:
        _main(normalizer_child, "values.parquet")
    except BaseException as error:  # noqa: BLE001 - PID1 must fail closed
        lifecycle.fail_closed(error)
