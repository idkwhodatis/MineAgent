"""Read-only native-factor physical closure proposal; NOT an active capability.

No caller of this module gains process/extraction/publication/cleanup authority.
Existing factor publication and SQL kinds remain unchanged and reject native_hdf
registration. Generic old backups may retain opaque .h5 orphans; this does not
establish registered semantic closure. Activate only atomically with genuine stopped-admission and
extraction witnesses, fenced shared-quota publication, schema and cold restore.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from backend.app.core.atomic_storage import _open_directory
from packages.factors.contracts import canonical_bytes
from packages.factors.native_publication_plan import CandidateNativeIntent
from packages.factors.v2_storage_contracts import (
    PublicationJournalV2,
    StorageIdentityV2,
)

MAX_NATIVE_BYTES = 16 * 1024**2
MAX_JOURNAL_BYTES = 16 * 1024**2
MAX_FILES = 10000
MAX_BYTES = 2 * 1024**3
OBJECT = re.compile(r"([a-f0-9]{64})\.(py|json|parquet|zip|h5)")
TEMP = re.compile(r"\.([a-f0-9]{64})\.(py|json|parquet|zip|h5)\.[a-f0-9]{32}\.tmp")
DATA = re.compile(r"(?:manifest|observations)\.json")
DATA_TEMP = re.compile(r"\.(?:manifest|observations)\.json\.[a-f0-9]{32}\.tmp")
JOURNAL_NAMES = {"publication.v2.json", "candidate-native.v1.json"}
JOURNAL_TEMP = re.compile(
    r"\.(?:publication\.v2|candidate-native\.v1)\.json\.[a-f0-9]{32}\.tmp"
)


@dataclass(frozen=True)
class PhysicalEntry:
    storage_key: str
    owner_id: UUID
    size_bytes: int
    sha256: str | None
    temporary: bool
    recovery_barrier: bool
    directory: bool = False


def _uuid(value):
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("Noncanonical owner/operation namespace")
    return parsed


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _classify(parts):
    """Closed layout only; extension does not establish semantic provenance."""
    owner = _uuid(parts[0])
    if len(parts) == 3 and parts[1] == "objects":
        final, temporary = OBJECT.fullmatch(parts[2]), TEMP.fullmatch(parts[2])
        match = final or temporary
        if match is not None:
            cap = MAX_NATIVE_BYTES if match[2] == "h5" else MAX_BYTES
            return (
                owner,
                temporary is not None,
                temporary is not None,
                cap,
                final[1] if final else None,
            )
    if len(parts) == 4 and parts[1] == "datasets":
        _uuid(parts[2])
        if DATA.fullmatch(parts[3]) or DATA_TEMP.fullmatch(parts[3]):
            temp = DATA_TEMP.fullmatch(parts[3]) is not None
            return owner, temp, temp, MAX_BYTES, None
    if len(parts) == 4 and parts[1] == "staging":
        _uuid(parts[2])
        if parts[3] in JOURNAL_NAMES or JOURNAL_TEMP.fullmatch(parts[3]):
            return (
                owner,
                parts[3] not in JOURNAL_NAMES,
                True,
                MAX_JOURNAL_BYTES,
                None,
            )
    raise ValueError("Unknown factor physical file")


def inventory(root, *, max_entries=MAX_FILES, max_bytes=MAX_BYTES, timeout_seconds=5.0):
    """Read all bytes through pinned no-follow descriptors, including orphans.

    Does not take publication locks or certify a quiescent backup. Callers must
    retain the existing maintenance/storage/SQL admission across reconciliation.
    Detected races, unsupported staging or unsafe entries block progress. Without
    external guards a sequential scan cannot exclude later changes to closed files.
    """
    if type(max_entries) is not int or not 1 <= max_entries <= MAX_FILES:
        raise ValueError("Invalid inventory entry ceiling")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError("Invalid inventory byte ceiling")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
        raise ValueError("Invalid inventory time ceiling")
    deadline = time.monotonic() + timeout_seconds

    def check_time():
        if time.monotonic() >= deadline:
            raise ValueError("Physical inventory time ceiling")

    path = Path(root).absolute() / "factors"
    fd = _open_directory(path)
    records, count, total = [], 0, 0

    def scan(directory, parts):
        nonlocal count, total
        check_time()
        before = os.fstat(directory)
        with os.scandir(directory) as entries:
            for item in entries:
                check_time()
                count += 1
                if count > max_entries:
                    raise ValueError("Physical inventory entry ceiling")
                childparts = (*parts, item.name)
                child = os.open(
                    item.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
                try:
                    info = os.fstat(child)
                    if stat.S_ISDIR(info.st_mode):
                        if len(childparts) == 1:
                            _uuid(item.name)
                        elif len(childparts) == 2:
                            if item.name not in {"objects", "datasets", "staging"}:
                                raise ValueError("Unknown factor namespace")
                        elif len(childparts) == 3 and parts[1] in {
                            "datasets",
                            "staging",
                        }:
                            _uuid(item.name)
                        else:
                            raise ValueError("Unexpected nested factor directory")
                        records.append(
                            PhysicalEntry(
                                "factors/" + "/".join(childparts) + "/",
                                _uuid(childparts[0]),
                                0,
                                None,
                                False,
                                len(childparts) == 3 and parts[1] == "staging",
                                True,
                            )
                        )
                        scan(child, childparts)
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Unsafe factor physical inode")
                    owner, temporary, barrier, cap, expected_hash = _classify(
                        childparts
                    )
                    if info.st_size > cap or info.st_size + total > max_bytes:
                        raise ValueError("Physical inventory byte ceiling")
                    digest, size = hashlib.sha256(), 0
                    while data := os.read(child, min(65536, info.st_size + 1 - size)):
                        check_time()
                        size += len(data)
                        if size > info.st_size:
                            raise ValueError("Physical file grew during inventory")
                        digest.update(data)
                    if size != info.st_size or _identity(info) != _identity(
                        os.fstat(child)
                    ):
                        raise ValueError("Physical file changed during inventory")
                    check_time()
                    sha = digest.hexdigest()
                    if expected_hash is not None and expected_hash != sha:
                        raise ValueError("Physical object content differs from name")
                    total += size
                    records.append(
                        PhysicalEntry(
                            "factors/" + "/".join(childparts),
                            owner,
                            size,
                            sha,
                            temporary,
                            barrier,
                        )
                    )
                finally:
                    os.close(child)
        check_time()
        if _identity(before) != _identity(os.fstat(directory)):
            raise ValueError("Factor directory changed during inventory")

    try:
        before = os.fstat(fd)
        scan(fd, ())
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Factor root replaced during inventory")
        check_time()
    finally:
        os.close(fd)
    check_time()
    return tuple(sorted(records, key=lambda record: record.storage_key))


@dataclass(frozen=True)
class PublicationFootprint:
    object_peak_bytes: int
    journal_peak_bytes: int
    peak_bytes: int
    peak_entries: int


def publication_footprint(journal):
    """Completed evidence-bundle charge proposal; not per-candidate admission.

    Charge final AND temporary object/journal copies; include four possibly-new
    directories (owner, objects, staging, operation). Existing copies do not give
    refunds. All uncertain bytes remain charged until independent reconciliation.
    """
    journal = PublicationJournalV2.model_validate(journal)
    raw = canonical_bytes(journal)
    if len(raw) > MAX_JOURNAL_BYTES:
        raise ValueError("Native journal byte ceiling")
    objects = 2 * journal.inventory.total_bytes
    journals = 2 * len(raw)
    return PublicationFootprint(
        objects,
        journals,
        objects + journals,
        2 * len(journal.inventory.objects) + 2 + 4,
    )


def verify_native_physical_closure(root, identities):
    """Verify opaque registered bytes; retain and return unregistered HDF orphans.

    This is NOT database restore acceptance or native format/stop verification.
    Journals and temps always remain recovery barriers, even state='recorded'.
    No unlink, cleanup, transaction or writer capability is provided.
    """
    parsed = []
    for item in identities:
        if len(parsed) >= MAX_FILES:
            raise ValueError("Native registration count ceiling")
        parsed.append(StorageIdentityV2.model_validate(item))
    identities = tuple(parsed)
    if not 1 <= len(identities) <= MAX_FILES:
        raise ValueError("Native registration count ceiling")
    if any(item.object.object_kind != "native_hdf" for item in identities):
        raise ValueError("Expected distinct native_hdf registrations")
    keys = {item.storage_key for item in identities}
    if len(keys) != len(identities) or len(
        {item.object.object_id for item in identities}
    ) != len(identities):
        raise ValueError("Repeated native registration identity")
    physical = inventory(root)
    if any(item.recovery_barrier for item in physical):
        raise ValueError("Independent publication recovery required")
    by_key = {item.storage_key: item for item in physical}
    for identity in identities:
        row = by_key.get(identity.storage_key)
        if row is None or (row.owner_id, row.sha256, row.size_bytes) != (
            identity.owner_id,
            identity.object.sha256,
            identity.object.size_bytes,
        ):
            raise ValueError("Native registration physical drift")
    return tuple(
        item
        for item in physical
        if item.storage_key.endswith(".h5") and item.storage_key not in keys
    )


def candidate_publication_footprint(intent):
    """Pre-normalizer single-HDF charge proposal, never a persisted reservation.

    No future normalized values/snapshot is required, avoiding a circular input
    dependency. The references in this DTO are declarations, NOT verified stops.
    """
    intent = CandidateNativeIntent.model_validate(intent)
    raw = canonical_bytes(intent)
    if len(raw) > 65536:
        raise ValueError("Candidate intent metadata ceiling")
    objects, journals = 2 * intent.output.object.size_bytes, 2 * len(raw)
    return PublicationFootprint(objects, journals, objects + journals, 8)
