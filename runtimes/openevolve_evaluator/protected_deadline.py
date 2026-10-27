"""Fixed60s native evaluator PID1; no evaluator/native imports before uid drop.

Reuses the reviewed namespace quiescence implementation, not its10s admission
profile. This distinct60s profile requires its own actual containment acceptance.
"""

import ctypes
import os
import resource
import signal
import stat
import sys
import time

from runtimes.hostile_deadline import deadline as lifecycle

CHILD_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "PYTHONPATH": "/opt/tradegrad",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "POLARS_MAX_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
    "XDG_CACHE_HOME": "/tmp/cache",
    "TRADEGRAD_DATA_WORKERS": "1",
    "AKQUANT_DATA_CACHE": "/tmp/cache",
}
CHILD_ARGS = ("/usr/local/bin/python", "-s", "-P", "-m", "entrypoint")


def remaining_seconds(deadline_ms, now_ms):
    if (
        type(deadline_ms) is not str
        or not deadline_ms.isascii()
        or not deadline_ms.isdecimal()
        or len(deadline_ms) > 16
        or type(now_ms) is not int
    ):
        raise ValueError("Invalid absolute native deadline")
    remaining = int(deadline_ms) - now_ms
    if not 0 < remaining <= 60000:
        raise ValueError("Expired or excessive native deadline")
    return remaining / 1000


def _capture_files():
    directory = os.open("/status", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = []
    try:
        info = os.fstat(directory)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("Native capture namespace differs")
        for name in ("stdout.bin", "stderr.bin"):
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            opened.append(fd)
        return tuple(opened)
    except BaseException:
        for fd in opened:
            os.close(fd)
        raise
    finally:
        os.close(directory)


def _child(libc, stdout, stderr):
    os.setsid()
    os.setgroups([])
    os.setresgid(65533, 65533, 65533)
    os.setresuid(65533, 65533, 65533)
    lifecycle.drop_capabilities(libc)
    lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
    lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
    lifecycle.require_caps(0)
    if (
        os.getresuid() != (65533,) * 3
        or os.getresgid() != (65533,) * 3
        or os.getgroups()
    ):
        raise RuntimeError("Native child credentials failed")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**2,) * 2)
    null = os.open("/dev/null", os.O_RDONLY)
    os.dup2(null, 0)
    os.dup2(stdout, 1)
    os.dup2(stderr, 2)
    lifecycle.close_fds()
    os.chdir("/tmp")
    # -s/-P plus exact readonly PYTHONPATH preserve original hash seed while
    # excluding user-site and cwd. Neither module nor env comes from the request.
    os.execve(CHILD_ARGS[0], CHILD_ARGS, dict(CHILD_ENV))


def _helper(role, expires, child_exit_code=0):
    from runtimes.openevolve_evaluator.protected_output import export_output

    try:
        if role != "export" or time.monotonic() >= expires:
            raise RuntimeError("Invalid native protected helper")
        libc = ctypes.CDLL(None, use_errno=True)
        lifecycle.drop_capabilities(libc)
        lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
        lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
        lifecycle.require_caps(0)
        lifecycle.close_fds()
        export_output(child_exit_code)
    except BaseException:  # noqa: BLE001 - never delay namespace death
        os._exit(125)
    os._exit(0)


def main():
    if (
        os.getpid() != 1
        or os.getresuid() != (0, 0, 0)
        or os.getresgid() != (0, 0, 0)
        or len(sys.argv) != 2
        or not sys.flags.isolated
    ):
        raise RuntimeError("Native profile requires fixed private root PID1")
    remaining = remaining_seconds(sys.argv[1], time.time_ns() // 1_000_000)
    expires = time.monotonic() + remaining
    libc = ctypes.CDLL(None, use_errno=True)
    lifecycle.prctl(libc, lifecycle.PR_SET_DUMPABLE, 0)
    lifecycle.prctl(libc, lifecycle.PR_SET_NO_NEW_PRIVS, 1)
    lifecycle.require_caps(lifecycle.BOOTSTRAP_MASK)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: os._exit(124))
    stdout, stderr = _capture_files()
    lifecycle.helper = _helper
    child = os.fork()
    if child == 0:
        try:
            _child(libc, stdout, stderr)
        except BaseException as error:  # noqa: BLE001 - kill namespace on any bootstrap failure
            lifecycle.fail_closed(error)
    os.close(stdout)
    os.close(stderr)
    lifecycle.drop_capabilities(libc, lifecycle.KILL_MASK)
    lifecycle.require_caps(lifecycle.KILL_MASK)
    while True:
        if time.monotonic() >= expires:
            os._exit(124)
        done, result = os.waitpid(child, os.WNOHANG)
        if done:
            lifecycle.quiesce_and_hold(expires, os.waitstatus_to_exitcode(result))
        time.sleep(min(0.01, max(0, expires - time.monotonic())))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:  # noqa: BLE001 - fixed PID1 must fail closed
        lifecycle.fail_closed(error)
