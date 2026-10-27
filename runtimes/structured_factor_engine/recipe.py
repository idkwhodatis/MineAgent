"""Exact protected-wrapper build-input identity, not image acceptance authority."""

import hashlib
import os
import stat
from pathlib import Path

from packages.research_runtime.process_contracts import canonical

FILES = (
    "runtimes/structured_factor_engine/Dockerfile",
    "runtimes/structured_factor_engine/Dockerfile.dockerignore",
    "runtimes/structured_factor_engine/trusted_parent.pth",
    "packages/structured_factor_strategy/__init__.py",
    "packages/structured_factor_strategy/contracts.py",
    "packages/structured_factor_strategy/operator.py",
    "packages/structured_factor_strategy/wire.py",
    "packages/factors/__init__.py",
    "packages/factors/contracts.py",
    "packages/factors/execution_snapshot.py",
    "runtimes/structured_factor_engine/__init__.py",
    "runtimes/structured_factor_engine/entrypoint.py",
    "runtimes/structured_factor_engine/runtime.py",
    "runtimes/structured_factor_engine/protected_deadline.py",
    "runtimes/structured_factor_engine/protected_output.py",
    "runtimes/hostile_deadline/contract.py",
    "runtimes/hostile_deadline/deadline.py",
)


def wrapper_inventory(root=None):
    root = Path(root) if root is not None else Path(__file__).parents[2]
    rows = []
    for name in sorted(FILES):
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or not 0 < before.st_size <= 131072
            ):
                raise ValueError("Invalid wrapper build input")
            raw = bytearray()
            while len(raw) <= 131072:
                block = os.read(fd, min(65536, 131073 - len(raw)))
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(fd)
            if (
                len(raw) != before.st_size
                or len(raw) > 131072
                or any(
                    getattr(before, k) != getattr(after, k)
                    for k in (
                        "st_dev",
                        "st_ino",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                        "st_mode",
                        "st_nlink",
                    )
                )
            ):
                raise ValueError("Wrapper build input changed")
            rows.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                }
            )
        finally:
            os.close(fd)
    return {
        "schema": "mineagent.structured-factor-engine-wrapper-inventory.v1",
        "files": rows,
    }


def wrapper_inventory_sha256(root=None):
    return hashlib.sha256(canonical(wrapper_inventory(root))).hexdigest()


if __name__ == "__main__":
    print(wrapper_inventory_sha256())


def dependency_lock_sha256(root=None):
    root = Path(root) if root is not None else Path(__file__).parents[2]
    names = (
        "runtimes/evaluator/requirements.lock",
        "runtimes/portable_engine/calendar-requirements.lock",
    )
    values = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names
    }
    return hashlib.sha256(canonical(values)).hexdigest()
