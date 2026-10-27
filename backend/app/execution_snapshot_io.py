"""Bounded opaque snapshot publication; no database or semantic authority."""

import hashlib
import time
from pathlib import Path

from backend.app.core.atomic_storage import publish_bytes
from backend.app.factor_native_io import read_exact, require_native_storage_guard
from packages.factors.contracts import canonical_bytes
from packages.factors.execution_snapshot import (
    MAX_SNAPSHOT_BYTES,
    ExecutionPublicationJournal,
)


def inventory(journal):
    journal = ExecutionPublicationJournal.model_validate(journal)
    return (
        journal.manifest_object,
        journal.manifest.assembled_rows,
        *(p.values for p in journal.manifest.partitions),
    )


def journal_address(journal):
    journal = ExecutionPublicationJournal.model_validate(journal)
    raw = canonical_bytes(journal)
    if len(raw) > 2_097_152:
        raise ValueError("Execution snapshot journal metadata bound")
    sha = hashlib.sha256(raw).hexdigest()
    return f"factors/{journal.manifest.owner_id}/objects/{sha}.json", sha, raw


def verify_snapshot_bytes(root, journal):
    require_native_storage_guard(root)
    deadline = time.monotonic() + 30
    key, sha, raw = journal_address(journal)
    read_exact(root, key, sha, len(raw), maximum=2_097_152)
    for obj in inventory(journal):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Execution snapshot I/O deadline")
        read_exact(
            root,
            obj.storage_key,
            obj.sha256,
            obj.size_bytes,
            maximum=MAX_SNAPSHOT_BYTES,
            timeout_seconds=min(10, remaining),
        )
    if time.monotonic() >= deadline:
        raise ValueError("Execution snapshot I/O deadline")


def publish_snapshot_bytes(root, journal, physical):
    require_native_storage_guard(root)
    objects = inventory(journal)
    if set(physical) != {obj.storage_key for obj in objects}:
        raise ValueError("Execution physical inventory differs")
    for obj in objects:
        raw = physical[obj.storage_key]
        if (
            type(raw) is not bytes
            or len(raw) != obj.size_bytes
            or hashlib.sha256(raw).hexdigest() != obj.sha256
        ):
            raise ValueError("Execution physical bytes differ")
    key, sha, raw = journal_address(journal)
    # SQL pre-admission owns this expected hash before the first external write.
    # This helper never removes an old journal, object or unknown temporary.
    publish_bytes(Path(root) / key, raw, sha256=sha, durable_root=Path(root))
    for obj in objects:
        publish_bytes(
            Path(root) / obj.storage_key,
            physical[obj.storage_key],
            sha256=obj.sha256,
            durable_root=Path(root),
        )
    verify_snapshot_bytes(root, journal)
