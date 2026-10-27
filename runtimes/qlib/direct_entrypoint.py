"""Fixed isolated analysis child; never invoked in the API/host interpreter.

No user code, provider initialization, workspace loading or network is requested.
The parent protected lifecycle must enforce credentials, deadline and extraction.
"""

import os
import stat
import sys

from packages.factors.contracts import canonical_bytes
from packages.qlib_adapter.direct_wire import MAX_INPUT_BYTES, read_input, read_result


def read_fixed_input():
    fd = os.open("/input/analysis.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_INPUT_BYTES
        ):
            raise ValueError("Invalid fixed analysis input")
        raw = bytearray()
        while len(raw) <= MAX_INPUT_BYTES:
            chunk = os.read(fd, min(65536, MAX_INPUT_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        identity = lambda s: (
            s.st_dev,
            s.st_ino,
            s.st_mode,
            s.st_uid,
            s.st_gid,
            s.st_nlink,
            s.st_size,
            s.st_mtime_ns,
            s.st_ctime_ns,
        )
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise ValueError("Analysis input changed")
        return read_input(bytes(raw))
    finally:
        os.close(fd)


def main():
    if len(sys.argv) != 1 or not sys.flags.isolated:
        raise ValueError("Invalid fixed Qlib invocation")
    import importlib.metadata

    if importlib.metadata.version("pyqlib") != "0.9.8":
        raise ValueError("Installed Qlib version differs")
    inputs = read_fixed_input()
    # Only the already credential-separated child reaches native dependencies.
    from packages.qlib_adapter.direct import analyze_direct

    result = analyze_direct(
        inputs.protocol,
        inputs.factors,
        inputs.labels,
        calendar_sessions=inputs.calendar_sessions,
    )
    raw = canonical_bytes(result)
    read_result(raw, inputs)
    fd = os.open(
        "/output/analysis.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
