"""Bounded private storage: resource IDs, immutable hashes and no symlink traversal."""

import errno
import hashlib
import os
import re
import stat
from pathlib import Path

from backend.app.core.atomic_storage import _open_directory
from backend.app.services import DomainError

SENSITIVE_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._~-]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)((?:password|secret|api[_-]?key|access[_-]?token|database_url)\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"postgres(?:ql)?(?:\+psycopg)?://[^\s]+"), "[REDACTED_DATABASE_URL]"),
]


def redact(value: str) -> str:
    for pattern, replacement in SENSITIVE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def safe_path(root: Path, key: str) -> Path:
    root = root.resolve()
    candidate = root / key
    if Path(key).is_absolute() or ".." in Path(key).parts:
        raise DomainError("unsafe_storage_path", "Invalid stored artifact path", 500)
    current = root
    for part in Path(key).parts:
        current = current / part
        if current.is_symlink():
            raise DomainError(
                "unsafe_storage_path", "Symlink storage entries are not allowed", 500
            )
    if not candidate.resolve().is_relative_to(root):
        raise DomainError(
            "unsafe_storage_path", "Artifact lies outside private storage", 500
        )
    return candidate


def _matches_open_content(fd: int, content: bytes) -> bool:
    """Check the pinned inode again without a second artifact-sized allocation.

    Equal-length writes can share a filesystem timestamp tick. Metadata alone
    cannot detect them. This remains a bounded read observation, not a lock
    against writers changing the file after verification returns.
    """
    offset = 0
    while offset < len(content):
        chunk = os.pread(fd, min(1024 * 1024, len(content) - offset), offset)
        if not chunk or chunk != content[offset : offset + len(chunk)]:
            return False
        offset += len(chunk)
    return os.pread(fd, 1, offset) == b""


def read_verified(root: Path, key: str, expected_sha256: str, max_bytes: int) -> bytes:
    """Read through pinned, no-follow directories, including the storage root.

    A pathname preflight alone cannot prevent a parent being replaced before
    open. O_NONBLOCK is also required: opening a FIFO must not hang before its
    type can be checked. Integrity is checked on the bytes and their open inode.
    """
    root, relative = Path(root).absolute(), Path(key)
    if relative.is_absolute() or ".." in relative.parts or ".." in root.parts:
        raise DomainError("unsafe_storage_path", "Invalid stored artifact path", 500)
    if not relative.parts or type(max_bytes) is not int or max_bytes < 0:
        raise DomainError("invalid_artifact", "Invalid artifact read bounds", 409)
    path = root / relative
    try:
        directory = _open_directory(path.parent)
        try:
            fd = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= max_bytes:
                    raise DomainError(
                        "invalid_artifact",
                        "Artifact is not a bounded regular file",
                        409,
                    )
                content = stream.read(max_bytes + 1)
                matches = len(content) == info.st_size and _matches_open_content(
                    stream.fileno(), content
                )
                final = os.fstat(stream.fileno())
                current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)

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
                    not matches
                    or identity(info) != identity(final)
                    or identity(info) != identity(current)
                ):
                    raise DomainError(
                        "artifact_integrity", "Artifact changed while being read", 409
                    )
        finally:
            os.close(directory)
    except FileNotFoundError:
        raise DomainError(
            "artifact_missing",
            "Artifact bytes are unavailable; restore the matching storage backup",
            409,
        ) from None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise DomainError(
                "unsafe_storage_path", "Symlink storage entries are not allowed", 500
            ) from None
        raise
    if (
        len(content) > max_bytes
        or hashlib.sha256(content).hexdigest() != expected_sha256
    ):
        raise DomainError(
            "artifact_integrity",
            "Artifact content does not match its immutable checksum",
            409,
        )
    return content
