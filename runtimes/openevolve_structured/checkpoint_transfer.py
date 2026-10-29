"""Stable, bounded native checkpoint byte transfer; no host publication authority.

The host reserves the declared complete inventory before accepting any chunk.
Each file is transferred in canonical fixed 64KiB chunks. Socket uncertainty is
terminal; no client retries. Host exact historical receipt delivery is separate.
Only trusted installed controller code calls this after native writer closure.
"""

import base64
import hashlib
import math
import os
import re
import stat
import time
from pathlib import Path
from uuid import UUID

CHUNK_BYTES = 65536
MAX_FILE_BYTES = 524288
MAX_TOTAL_BYTES = 8388608
MAX_FILES = 11  # fixed maximum_population=8 plus three native metadata/best files
FIXED = frozenset({"metadata.json", "best_program.json", "best_program_info.json"})
PROGRAM = re.compile(r"programs/([0-9a-f-]{36})\.json\Z")


def _name(name):
    if type(name) is not str:
        raise ValueError("checkpoint name type")
    if name in FIXED:
        return name
    match = PROGRAM.fullmatch(name)
    if match is None or str(UUID(match[1])) != match[1]:
        raise ValueError("checkpoint closed filename")
    return name


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


def _deadline(deadline):
    if (
        type(deadline) not in (int, float)
        or not math.isfinite(deadline)
        or time.monotonic() >= deadline
    ):
        raise ValueError("checkpoint original deadline")


def _directory(name, parent=None):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def read_checkpoint(root, token, *, deadline):
    """Actual closed native files; root must be the fixed /work/checkpoints dir.

    Root may be supplied by a test driver, never an incoming host request. Every
    ancestor is opened nofollow, then child names are resolved from retained FDs.
    Returns immutable raw bytes, not parsed/executable native objects.
    """
    if type(token) is not UUID:
        raise ValueError("checkpoint token")
    _deadline(deadline)
    root = Path(root).absolute()
    fd = _directory("/")
    try:
        for component in root.parts[1:]:
            child = _directory(component, fd)
            os.close(fd)
            fd = child
        directory = _directory(str(token), fd)
        try:
            before = os.fstat(directory)
            if set(os.listdir(directory)) != FIXED | {"programs"}:
                raise ValueError("checkpoint root inventory")
            programs = _directory("programs", directory)
            try:
                programs_before = os.fstat(programs)
                names = sorted(os.listdir(programs))
                if not 1 <= len(names) <= MAX_FILES - 3:
                    raise ValueError("checkpoint program count")
                names = sorted([*FIXED, *(f"programs/{name}" for name in names)])
                contents = []
                total = 0
                for name in names:
                    _deadline(deadline)
                    _name(name)
                    parent, leaf = (
                        (programs, name[9:])
                        if name.startswith("programs/")
                        else (directory, name)
                    )
                    file = os.open(
                        leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                    )
                    try:
                        first = os.fstat(file)
                        if (
                            not stat.S_ISREG(first.st_mode)
                            or first.st_nlink != 1
                            or not 1 <= first.st_size <= MAX_FILE_BYTES
                        ):
                            raise ValueError("checkpoint regular bounded singleton")
                        raw = bytearray()
                        while chunk := os.read(
                            file, min(CHUNK_BYTES, MAX_FILE_BYTES + 1 - len(raw))
                        ):
                            _deadline(deadline)
                            raw.extend(chunk)
                            if len(raw) > MAX_FILE_BYTES:
                                raise ValueError("checkpoint file bound")
                        if (
                            _identity(first) != _identity(os.fstat(file))
                            or _identity(first)
                            != _identity(
                                os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                            )
                            or len(raw) != first.st_size
                        ):
                            raise ValueError("checkpoint file changed")
                        total += len(raw)
                        if total > MAX_TOTAL_BYTES:
                            raise ValueError("checkpoint total bound")
                        contents.append((name, bytes(raw)))
                    finally:
                        os.close(file)
                if (
                    _identity(before) != _identity(os.fstat(directory))
                    or _identity(before)
                    != _identity(os.stat(str(token), dir_fd=fd, follow_symlinks=False))
                    or _identity(programs_before) != _identity(os.fstat(programs))
                    or _identity(programs_before)
                    != _identity(
                        os.stat("programs", dir_fd=directory, follow_symlinks=False)
                    )
                ):
                    raise ValueError("checkpoint directory changed")
                _deadline(deadline)
                return tuple(contents)
            finally:
                os.close(programs)
        finally:
            os.close(directory)
    finally:
        os.close(fd)


def transfer_checkpoint(send, *, token, files, deadline):
    """send is fixed installed transport, not a provider/host authority callback."""
    if (
        type(token) is not UUID
        or type(files) is not tuple
        or not 4 <= len(files) <= MAX_FILES
    ):
        raise ValueError("checkpoint transfer shape")
    names = []
    inventory = []
    for name, raw in files:
        _name(name)
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_FILE_BYTES:
            raise ValueError("checkpoint transfer bytes")
        names.append(name)
        inventory.append(
            {
                "name": name,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    if (
        names != sorted(set(names))
        or not FIXED <= set(names)
        or sum(x["size_bytes"] for x in inventory) > MAX_TOTAL_BYTES
    ):
        raise ValueError("checkpoint inventory bound")
    _deadline(deadline)
    # Complete bounded declaration is sent before first payload byte. Only the
    # host's committed reservation ACK permits transport to continue.
    ack = send("checkpoint_upload_begin", {"token": str(token), "files": inventory})
    if ack != {"token": str(token), "reserved": True} or ack["reserved"] is not True:
        raise ValueError("checkpoint reservation ACK")
    sequence = 0
    for name, raw in files:
        for offset in range(0, len(raw), CHUNK_BYTES):
            _deadline(deadline)
            chunk = raw[offset : offset + CHUNK_BYTES]
            digest = hashlib.sha256(chunk).hexdigest()
            ack = send(
                "checkpoint_upload_chunk",
                {
                    "token": str(token),
                    "sequence": sequence,
                    "name": name,
                    "offset": offset,
                    "sha256": digest,
                    "data_base64": base64.b64encode(chunk).decode("ascii"),
                },
            )
            if (
                ack != {"token": str(token), "sequence": sequence, "sha256": digest}
                or type(ack["sequence"]) is not int
            ):
                raise ValueError("checkpoint chunk ACK")
            sequence += 1
    _deadline(deadline)
    ack = send("checkpoint_upload_finish", {"token": str(token), "chunks": sequence})
    if (
        type(ack) is not dict
        or set(ack) != {"token", "receipt_id"}
        or ack["token"] != str(token)
        or str(UUID(ack["receipt_id"])) != ack["receipt_id"]
    ):
        raise ValueError("checkpoint durable completion ACK")
    return ack["receipt_id"]
