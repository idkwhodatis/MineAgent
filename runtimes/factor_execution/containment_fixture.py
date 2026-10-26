"""Closed native-loaded containment fixtures, never executed on the host."""

import ctypes
import errno
import os
import signal
import time
from pathlib import Path

MODES = ("tree_quiescence", "tree_deadline", "no_reader", "collector_disconnected")
MARKER = b"mineagent-numeric-native-probes-completed-v1\n"


def main(mode):
    if mode not in MODES:
        raise ValueError("closed containment fixture required")
    import h5py
    import numpy

    assert h5py.version.hdf5_version and numpy.__version__
    assert os.getuid() == os.getgid() == 65533
    status = {
        key: value.rstrip()
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":\t" in line
        for key, value in [line.split(":\t", 1)]
    }
    assert all(
        int(status[key], 16) == 0 for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")
    )
    assert status["NoNewPrivs"] == "1" and status["Seccomp"] == "2"
    if mode in ("no_reader", "collector_disconnected"):
        with open("/output/result.h5", "xb") as output:
            os.fchmod(output.fileno(), 0o644)
            block = b"x" * 65536
            for _ in range(256):
                assert output.write(block) == len(block)
        time.sleep(0.5)
        return
    for change in (lambda: os.setuid(0), lambda: os.setgid(0)):
        try:
            change()
        except PermissionError:
            pass
        else:
            raise AssertionError("regained privileged identity")
    for signum in (signal.SIGSTOP, signal.SIGKILL, signal.SIGTERM):
        try:
            os.kill(1, signum)
        except PermissionError:
            pass
        else:
            raise AssertionError("signalled protected PID1")
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.ptrace(16, 1, 0, 0) == -1 and ctypes.get_errno() == errno.EPERM
    for attempt in (
        lambda: os.open("/proc/1/mem", os.O_RDONLY),
        lambda: os.listdir("/proc/1/fd"),
    ):
        try:
            attempt()
        except PermissionError:
            pass
        else:
            raise AssertionError("inspected protected PID1 memory/FDs")
    assert all(int(fd) < 4 for fd in os.listdir("/proc/self/fd"))
    for _ in range(3):
        if os.fork() == 0:
            os.setsid()
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(0.05)
    if mode == "tree_deadline":
        while True:
            time.sleep(0.05)
    with open("/output/result.h5", "xb") as output:
        os.fchmod(output.fileno(), 0o644)
        output.write(MARKER)
    # Host must observe all four real candidate identities before this parent exits.
    time.sleep(1.5)


if __name__ == "__main__":
    main(MODE)  # noqa: F821 - literal closed mode prepended by the host fixture driver
