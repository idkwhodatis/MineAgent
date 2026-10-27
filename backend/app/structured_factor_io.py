"""025 fixed-leaf retained market reads; no change to historical022 source reader."""

import hashlib
import os
import re
import stat
import time
from pathlib import Path

from backend.app.core.atomic_storage import _open_directory
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.services import DomainError
from packages.structured_factor_strategy.market import ARTIFACTS as MARKET_ARTIFACTS


def fail():
    raise DomainError(
        "structured_market_drift",
        "Exact retained structured market is unavailable",
        409,
    )


def read_structured_artifact(root, storage_key, name, expected_hash, maximum):
    """Fixed leaf under exact dataset directory, bounded stable nofollow read."""
    require_native_storage_guard(root)
    if (
        type(maximum) is not int
        or not 0 < maximum <= 32 * 1024**2
        or type(storage_key) is not str
        or re.fullmatch(r"datasets/[a-f0-9-]{36}/[a-f0-9-]{36}", storage_key) is None
        or name not in {"manifest.json", *MARKET_ARTIFACTS}
        or type(expected_hash) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        fail()
    deadline = time.monotonic() + 10
    parent = _open_directory(Path(root).absolute() / storage_key)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or not 0 < before.st_size <= maximum
            ):
                fail()
            content = bytearray()
            while chunk := os.read(fd, min(65536, maximum + 1 - len(content))):
                if time.monotonic() >= deadline:
                    fail()
                content.extend(chunk)
                if len(content) > maximum:
                    fail()
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            identity = lambda value: (
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
                or len(content) != before.st_size
                or hashlib.sha256(content).hexdigest() != expected_hash
                or time.monotonic() >= deadline
            ):
                fail()
            return bytes(content)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
