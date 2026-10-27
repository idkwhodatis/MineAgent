"""Fresh physical native stopped-byte check, not a detached DB/financial grant.

Caller must retain the application lifecycle/storage guard and independently
resolve current fenced admission, reservation, input object, and every immutable
receipt row. This function never launches or interprets native code.
"""

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from packages.research_runtime.process_contracts import ProcessError, canonical
from packages.research_runtime.process_declarations_v2 import ProcessDeclarationV2
from runtimes.openevolve_evaluator.journal import (
    NativeExtraction,
    NativeIdentity,
    NativeLaunch,
    NativeStop,
    validate_native_journal,
)
from runtimes.openevolve_evaluator.protected_output import MAX_FRAME, validate_export
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.openevolve_native_supervisor import read_manifest
from runtimes.research_supervisor.supervisor import (
    _directory,
    operation_path,
    process_fence,
)


@dataclass(frozen=True)
class NativeStoppedBytes:
    launch: NativeLaunch
    identity: NativeIdentity
    stop: NativeStop
    extraction: NativeExtraction
    payload: bytes
    files: Mapping[str, bytes]


def _journal(root, operation, admission, *, require_output):
    def record(name, kind):
        return kind.model_validate_json(
            canonical(read_numeric_record(operation / "control" / name))
        )

    launch = record("launch.json", NativeLaunch)
    identity = record("identity.json", NativeIdentity)
    stop = record("stop.json", NativeStop)
    extraction = record("extraction.json", NativeExtraction) if require_output else None
    if launch.intent.admission != admission:
        raise ProcessError("oe_native_process_journal_identity")
    validate_native_journal(launch, identity, stop, extraction)
    rootfd = _directory(root)
    try:
        info = os.fstat(rootfd)
        if (info.st_dev, info.st_ino) != (launch.root_device, launch.root_inode):
            raise ProcessError("oe_native_process_root_identity")
    finally:
        os.close(rootfd)
    read_manifest(operation, launch)
    return launch, identity, stop, extraction


def read_native_journal(root, admission, *, require_output=False):
    if require_output:
        value = read_stopped_native_bytes(root, admission)
        return value.launch, value.identity, value.stop, value.extraction
    admission = ProcessDeclarationV2.model_validate(admission)
    with process_fence(root):
        return _journal(
            root, operation_path(root, admission), admission, require_output=False
        )


def read_stopped_native_bytes(root, admission):
    admission = ProcessDeclarationV2.model_validate(admission)
    with process_fence(root):
        operation = operation_path(root, admission)

        launch, identity, stop, extraction = _journal(
            root, operation, admission, require_output=True
        )
        directory = _directory(operation / "output")
        try:
            if set(os.listdir(directory)) != {"native-export.bin"}:
                raise ProcessError("oe_native_process_output")
            fd = os.open(
                "native-export.bin",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_size != extraction.size_bytes
                    or not 0 < before.st_size <= MAX_FRAME
                ):
                    raise ProcessError("oe_native_process_output")
                raw = bytearray()
                while len(raw) <= MAX_FRAME:
                    chunk = os.read(fd, min(65536, MAX_FRAME + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                after = os.fstat(fd)
                if (
                    len(raw) != before.st_size
                    or len(raw) > MAX_FRAME
                    or any(
                        getattr(before, k) != getattr(after, k)
                        for k in (
                            "st_dev",
                            "st_ino",
                            "st_uid",
                            "st_mode",
                            "st_nlink",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                        )
                    )
                ):
                    raise ProcessError("oe_native_process_output_changed")
            finally:
                os.close(fd)
        finally:
            os.close(directory)
        payload = bytes(raw)
        if hashlib.sha256(payload).hexdigest() != extraction.sha256:
            raise ProcessError("oe_native_process_output")
        files = validate_export(payload)
        return NativeStoppedBytes(
            launch, identity, stop, extraction, payload, MappingProxyType(files)
        )
