"""Fixed isolated child. Protected parent owns deadline, reaping and extraction."""

import os
import stat
import sys

from packages.structured_factor_strategy.contracts import (
    MAX_INPUT_BYTES,
    MAX_MARKET_BYTES,
)


def read_fixed(name, maximum):
    if name not in {"strategy.json", "market.json"}:
        raise ValueError("Unknown fixed structured input")
    fd = os.open("/input/" + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError("Unsafe structured input")
        raw = bytearray()
        while len(raw) <= maximum:
            part = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        named = os.stat("/input/" + name, follow_symlinks=False)
        identity = lambda s: (
            s.st_dev,
            s.st_ino,
            s.st_mode,
            s.st_nlink,
            s.st_size,
            s.st_mtime_ns,
            s.st_ctime_ns,
        )
        if (
            len(raw) != before.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(named)
        ):
            raise ValueError("Structured input changed")
        return bytes(raw)
    finally:
        os.close(fd)


def main():
    if len(sys.argv) != 1 or not sys.flags.isolated:
        raise ValueError("Fixed isolated structured invocation required")
    from runtimes.structured_factor_engine.runtime import execute_structured

    raw = execute_structured(
        read_fixed("strategy.json", MAX_INPUT_BYTES),
        read_fixed("market.json", MAX_MARKET_BYTES),
    )
    fd = os.open(
        "/output/result.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    try:
        os.fchmod(fd, 0o644)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ValueError("Structured result write stopped")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
