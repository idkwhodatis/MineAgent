"""Bounded stable non-following reads for the fixed read-only bootstrap mount."""

import os
import stat


def read_fixed(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise ValueError("fixed input bound/type")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, maximum + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > maximum:
                raise ValueError("fixed input growth")
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)

        def identity(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if (
            identity(before) != identity(after)
            or identity(after) != identity(named)
            or len(raw) != before.st_size
        ):
            raise ValueError("fixed input changed")
        return bytes(raw)
    finally:
        os.close(fd)
