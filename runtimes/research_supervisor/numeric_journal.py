"""Protected numeric journal reads. No launcher and no native parser.

Output is retained only as a bounded opaque file. Every path component and file
is opened without following links; callers retain lifecycle admission across I/O.
"""

import hashlib
import json
import os
import stat

from packages.research_runtime.numeric_process_journal import (
    MAX_OUTPUT,
    NumericExtraction,
    NumericIdentity,
    NumericLaunch,
    NumericStop,
    validate_numeric_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.supervisor import (
    _directory,
    operation_path,
)


def read_numeric_record(path):
    """Bounded immutable-control read with a post-read inode consistency check."""
    from pathlib import Path

    path = Path(path)
    parent = _directory(path.parent)
    fd = None
    try:
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent
        )
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 < before.st_size <= 16384
        ):
            raise ProcessError("numeric_process_journal")
        parts, remaining = [], 16385
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(parts)
        after = os.fstat(fd)
        fields = (
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
        if (
            not 0 < len(raw) <= 16384
            or len(raw) != before.st_size
            or any(getattr(before, key) != getattr(after, key) for key in fields)
        ):
            raise ProcessError("numeric_process_journal")

        def unique(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError("duplicate field")
                value[key] = item
            return value

        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict or canonical(value) != raw:
            raise ProcessError("numeric_process_journal")
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ProcessError("numeric_process_journal") from None
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def read_numeric_journal(root, admission, *, require_output=False):
    path = operation_path(root, admission)
    launch = NumericLaunch.model_validate_json(
        canonical(read_numeric_record(path / "control" / "launch.json"))
    )
    identity = NumericIdentity.model_validate_json(
        canonical(read_numeric_record(path / "control" / "identity.json"))
    )
    stop = NumericStop.model_validate_json(
        canonical(read_numeric_record(path / "control" / "stop.json"))
    )
    extraction = None
    if require_output:
        extraction = NumericExtraction.model_validate_json(
            canonical(read_numeric_record(path / "control" / "extraction.json"))
        )
    if launch.intent.admission != admission:
        raise ProcessError("numeric_process_journal_identity")
    fd = _directory(root)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (launch.root_device, launch.root_inode):
            raise ProcessError("numeric_process_root_identity")
    finally:
        os.close(fd)
    return validate_numeric_journal(launch, identity, stop, extraction)


def read_opaque_output(root, admission, extraction):
    """Not an authority API: service must resolve DB/physical receipts first."""
    extraction = NumericExtraction.model_validate(extraction)
    directory = _directory(operation_path(root, admission) / "output")
    fd = None
    try:
        fd = os.open(
            extraction.basename,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory,
        )
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != extraction.size_bytes
            or before.st_size > MAX_OUTPUT
        ):
            raise ProcessError("numeric_process_output_identity")
        chunks, remaining = [], extraction.size_bytes + 1
        while remaining:
            part = os.read(fd, min(65536, remaining))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        value = b"".join(chunks)
        after = os.fstat(fd)
        if (
            len(value) != extraction.size_bytes
            or hashlib.sha256(value).hexdigest() != extraction.sha256
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise ProcessError("numeric_process_output_identity")
        return value
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def validate_numeric_quiescence(operation, record):
    """Cold inventory validation; restored root device/inode may legitimately differ.

    This is not an output resolver or a new execution authority. Unknown/live or
    incomplete numeric journals fail maintenance admission closed.
    """
    launch = NumericLaunch.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "launch.json"))
    )
    if (str(launch.intent.admission.owner_id), str(launch.intent.admission.id)) != (
        operation.parent.name,
        operation.name,
    ):
        raise ProcessError("numeric_process_journal_identity")
    identity = NumericIdentity.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "identity.json"))
    )
    stop = NumericStop.model_validate_json(
        canonical(read_numeric_record(operation / "control" / "stop.json"))
    )
    validate_numeric_journal(launch, identity, stop)
