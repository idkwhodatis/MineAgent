"""Data-only stopped-history file reads; never live process or launch authority.

Restore creates owner-only read-only files (0400); original journals use0600.
Only historical services with terminal, settled database closure call here.
Live supervisor readers remain unchanged and require0600. No chmod or execution.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from backend.app.factor_native_io import require_native_storage_guard
from packages.research_runtime.numeric_process_journal import MAX_OUTPUT
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.supervisor import _directory, operation_path

_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_uid",
    "st_gid",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
_OUTPUTS = {
    "numeric": (frozenset({"result.h5", "values.parquet", "rows.json"}), MAX_OUTPUT),
    "analysis": (frozenset({"analysis.json"}), 1_048_576),
    "structured": (frozenset({"result.json"}), 16_777_216),
}


def _fail():
    raise ProcessError("historical_process_file_integrity")


def _read(root, path, maximum, *, size=None, sha256=None, exact_names=None):
    require_native_storage_guard(root)
    root, path = Path(root).absolute(), Path(path).absolute()
    if ".." in path.parts or not path.is_relative_to(root) or path == root:
        _fail()
    parent, fd = None, None
    try:
        parent = _directory(path.parent)
        if exact_names is not None and set(os.listdir(parent)) != exact_names:
            _fail()
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
        )
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) not in (0o400, 0o600)
            or not 0 < before.st_size <= maximum
            or (size is not None and before.st_size != size)
        ):
            _fail()
        chunks, remaining = [], before.st_size + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (
            (exact_names is not None and set(os.listdir(parent)) != exact_names)
            or len(raw) != before.st_size
            or any(getattr(before, f) != getattr(after, f) for f in _FIELDS)
            or any(getattr(before, f) != getattr(linked, f) for f in _FIELDS)
            or (sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256)
        ):
            _fail()
        require_native_storage_guard(root)
        return raw
    except OSError:
        _fail()
    finally:
        if fd is not None:
            os.close(fd)
        if parent is not None:
            os.close(parent)


def read_historical_record(root, path):
    raw = _read(root, path, 16384)

    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                _fail()
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or canonical(value) != raw:
            _fail()
        return value
    except (ValueError, TypeError, UnicodeError):
        _fail()


def read_historical_output(root, admission, *, kind, basename, size_bytes, sha256):
    """Exact bounded bytes only; caller must retain original typed journal checks."""
    if kind not in _OUTPUTS:
        _fail()
    names, maximum = _OUTPUTS[kind]
    if (
        basename not in names
        or type(size_bytes) is not int
        or not 0 < size_bytes <= maximum
        or type(sha256) is not str
        or len(sha256) != 64
        or any(c not in "0123456789abcdef" for c in sha256)
    ):
        _fail()
    return _read(
        root,
        operation_path(root, admission) / "output" / basename,
        maximum,
        size=size_bytes,
        sha256=sha256,
        exact_names={"result.json"} if kind == "structured" else None,
    )
