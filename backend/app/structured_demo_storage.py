"""Closed demo inventory in the existing owned dataset namespace.

Legacy names retain their old rules. A new filename never gains admission from
its spelling alone: an explicit canonical demo manifest and all bytes are needed.
Incomplete/orphan trees are retained and block publication; no repair/deletion.
"""

import hashlib
import json
import os
import stat

from packages.marketdata.normalize import canonical_json
from packages.structured_factor_strategy.demo import DEMO_MARKER, WINDOW
from packages.structured_factor_strategy.market import ARTIFACTS, stage_market

FILES = ARTIFACTS | {"manifest.json"}
MAX_FILE = 65536


def _read(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_FILE
        ):
            raise ValueError("Demo dataset requires bounded singleton regular files")
        raw = bytearray()
        while chunk := os.read(fd, min(8192, MAX_FILE + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > MAX_FILE:
                raise ValueError("Demo dataset file exceeds bound")
        after = os.fstat(fd)
        linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
        identity = lambda x: (
            x.st_dev,
            x.st_ino,
            x.st_mode,
            x.st_nlink,
            x.st_size,
            x.st_mtime_ns,
            x.st_ctime_ns,
        )
        if (
            identity(before) != identity(after)
            or identity(before) != identity(linked)
            or len(raw) != before.st_size
        ):
            raise ValueError("Demo dataset file changed")
        return bytes(raw)
    finally:
        os.close(fd)


def verify_demo_directory(directory, names):
    """Validate only a reserved demo identity; never relax any legacy inventory."""
    if set(names) != FILES:
        raise ValueError(
            "Exact demo file inventory required; partial trees need recovery"
        )
    raw = _read(directory, "manifest.json")
    manifest = json.loads(raw)
    if (
        type(manifest) is not dict
        or manifest.get("structured_demo") != DEMO_MARKER
        or canonical_json(manifest) != raw
    ):
        raise ValueError("Exact canonical demo manifest required")
    artifacts = {name: _read(directory, name) for name in ARTIFACTS}
    stage_market(
        raw, artifacts, dataset_sha256=hashlib.sha256(raw).hexdigest(), window=WINDOW
    )
    if manifest.get("row_count") != 12 or len(manifest.get("listings", [])) != 3:
        raise ValueError("Closed three-listing four-session demo required")
    return len(raw) + sum(map(len, artifacts.values()))


def demo_physical_usage(root_fd, owner_id, deadline, maximum_files):
    """Charge reserved per-owner demo identities through existing factor quota.

    Existing datasets retain their own format and are never opened/reclassified.
    A bounded nofollow owner-directory pass finds every deterministic demo ID,
    including bytes with no committed row. Incomplete ones fail closed rather
    than being silently repaired, dropped from accounting or automatically erased.
    """
    import time
    from uuid import UUID

    from packages.structured_factor_strategy.demo import dataset_id

    def check():
        if time.monotonic() >= deadline:
            raise ValueError("Demo inventory deadline")

    def directory(name, parent):
        return os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)

    total = owned = count = 0
    check()
    try:
        datasets = directory("datasets", root_fd)
    except FileNotFoundError:
        return 0, 0, 0
    try:
        initial = os.fstat(datasets)
        owners = os.listdir(datasets)
        if len(owners) > maximum_files:
            raise ValueError("Bounded dataset owner inventory required")
        for name in owners:
            check()
            if str(UUID(name)) != name:
                raise ValueError("Canonical dataset owner namespace required")
            owner = directory(name, datasets)
            try:
                before = os.fstat(owner)
                key = str(dataset_id(UUID(name)))
                try:
                    demo = directory(key, owner)
                except FileNotFoundError:
                    if identity(before) != identity(os.fstat(owner)) or identity(
                        before
                    ) != identity(
                        os.stat(name, dir_fd=datasets, follow_symlinks=False)
                    ):
                        raise ValueError("Dataset owner directory changed")
                    continue
                try:
                    baseline = os.fstat(demo)
                    names = os.listdir(demo)
                    size = verify_demo_directory(demo, names)
                    if identity(baseline) != identity(os.fstat(demo)) or identity(
                        baseline
                    ) != identity(os.stat(key, dir_fd=owner, follow_symlinks=False)):
                        raise ValueError("Demo directory changed")
                    total += size
                    if name == str(owner_id):
                        owned += size
                    count += len(names) + 3
                    if count > maximum_files:
                        raise ValueError("Demo physical inventory bound")
                finally:
                    os.close(demo)
                if identity(before) != identity(os.fstat(owner)) or identity(
                    before
                ) != identity(os.stat(name, dir_fd=datasets, follow_symlinks=False)):
                    raise ValueError("Dataset owner directory changed")
            finally:
                os.close(owner)
        if identity(initial) != identity(os.fstat(datasets)) or identity(
            initial
        ) != identity(os.stat("datasets", dir_fd=root_fd, follow_symlinks=False)):
            raise ValueError("Dataset namespace changed")
        check()
        return total, owned, count
    finally:
        os.close(datasets)
