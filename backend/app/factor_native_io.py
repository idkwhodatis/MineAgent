"""Opaque bounded I/O under a retained factor namespace fence; never HDF parsing.

This layer has no SQL/publication authority. Callers must retain lifecycle and
fresh-transaction authority before and after writes. Final files are never
removed on failure; a lost outcome remains conservatively charged.
"""

import fcntl
import hashlib
import os
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from backend.app.core.atomic_storage import _open_directory, publish_bytes
from backend.app.core.storage_barrier import storage_guard
from backend.app.factor_native_storage import _identity
from packages.factors.contracts import canonical_bytes
from packages.factors.native_publication import NativePublicationIntent
from packages.factors.v2_storage_contracts import validate_object_bytes

_NATIVE_GUARD = ContextVar("native_storage_guard", default=None)


class NativeStorageError(ValueError):
    pass


@contextmanager
def native_storage_guard(root):
    """Same nonblocking flock as legacy factor publication, retained across SQL."""
    root = Path(root).absolute()
    with storage_guard(root):
        fd = _open_directory(root / "factors")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = os.fstat(fd)
            current = os.stat(root / "factors", follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
                raise NativeStorageError("Factor namespace changed")
            token = _NATIVE_GUARD.set((root, fd, before.st_dev, before.st_ino))
            try:
                yield fd
            finally:
                _NATIVE_GUARD.reset(token)
            after = os.stat(root / "factors", follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise NativeStorageError("Factor namespace changed")
        finally:
            os.close(fd)


def require_native_storage_guard(root):
    state = _NATIVE_GUARD.get()
    root = Path(root).absolute()
    if state is None or state[0] != root:
        raise NativeStorageError("Retained native storage fence required")
    info = os.fstat(state[1])
    named = os.stat(root / "factors", follow_symlinks=False)
    if (info.st_dev, info.st_ino) != state[2:] or (named.st_dev, named.st_ino) != state[
        2:
    ]:
        raise NativeStorageError("Retained native storage fence changed")
    return state[1]


def read_exact(root, key, sha256, size, *, maximum=16_777_216, timeout_seconds=10):
    """Read only a canonical factors-relative address with bounded stable bytes."""
    from backend.app.factor_native_storage import _classify

    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
        raise NativeStorageError("Invalid native read deadline")
    deadline = time.monotonic() + timeout_seconds

    def check_time():
        if time.monotonic() >= deadline:
            raise NativeStorageError("Native read deadline exceeded")

    check_time()

    if type(key) is not str or key.split("/")[0] != "factors":
        raise NativeStorageError("Invalid native address")
    parts = key.split("/")
    if len(parts) == 4 and parts[2] == "native-journals":
        from uuid import UUID

        if (
            str(UUID(parts[1])) != parts[1]
            or not parts[3].endswith(".json")
            or str(UUID(parts[3][:-5])) != parts[3][:-5]
        ):
            raise NativeStorageError("Invalid native journal address")
    else:
        _classify(tuple(parts[1:]))
    if type(size) is not int or not 1 <= size <= maximum:
        raise NativeStorageError("Native byte bound exceeded")
    parent = _open_directory(Path(root).absolute() / Path(key).parent)
    try:
        fd = os.open(
            Path(key).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != size
                or before.st_mode & 0o222
            ):
                raise NativeStorageError("Unsafe native file")
            content = bytearray()
            while chunk := os.read(fd, min(65536, size + 1 - len(content))):
                check_time()
                content.extend(chunk)
                if len(content) > size:
                    raise NativeStorageError("Native byte bound exceeded")
            after = os.fstat(fd)
            named = os.stat(Path(key).name, dir_fd=parent, follow_symlinks=False)
            if _identity(before) != _identity(after) or _identity(after) != _identity(
                named
            ):
                raise NativeStorageError("Native file changed")
            if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
                raise NativeStorageError("Native byte identity differs")
            check_time()
            return bytes(content)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def publish_candidate_bytes(root, intent, content):
    """Atomic no-clobber write only; SQL authority belongs to the orchestrator."""
    intent = NativePublicationIntent.model_validate(intent)
    validate_object_bytes(intent.output, content)
    journal = canonical_bytes(intent)
    if len(journal) > 65536:
        raise NativeStorageError("Native journal bound exceeded")
    root = Path(root).absolute()
    publish_bytes(
        root / intent.journal_key,
        journal,
        sha256=hashlib.sha256(journal).hexdigest(),
        durable_root=root,
    )
    publish_bytes(
        root / intent.output.storage_key,
        content,
        sha256=intent.output.object.sha256,
        durable_root=root,
    )
    # Existing-address no-clobber replay must also reject links and unstable bytes.
    verify_candidate_bytes(root, intent)


def verify_candidate_bytes(root, intent):
    intent = NativePublicationIntent.model_validate(intent)
    journal = canonical_bytes(intent)
    read_exact(
        root,
        intent.journal_key,
        hashlib.sha256(journal).hexdigest(),
        len(journal),
        maximum=65536,
    )
    return read_exact(
        root,
        intent.output.storage_key,
        intent.output.object.sha256,
        intent.output.object.size_bytes,
    )
