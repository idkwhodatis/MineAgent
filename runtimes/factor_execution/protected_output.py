"""Disabled numeric opaque output ABI; separate from the stdlib proof profile.

Shares the reviewed nofollow inode-read design, not its result.bin/4MiB ABI.
No native parsing, runtime admission or positive-stop authority is granted here."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct

MAGIC = b"MANU2\x00"
MAX_OUTPUT = 16 * 1024 * 1024
OUTPUTS = ("result.h5", "values.parquet")
MAX_FRAME = MAX_OUTPUT + 1024


def read_regular(name: str, cap: int) -> bytes:
    if (
        type(name) is not str
        or name not in OUTPUTS
        or type(cap) is not int
        or not 0 < cap <= MAX_OUTPUT
    ):
        raise ValueError("fixed output ABI required")
    directory = os.open("/output", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o733:
            raise ValueError("output directory ownership/mode drift")
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != 65533
                or not before.st_mode & stat.S_IROTH
                or not 0 < before.st_size <= cap
            ):
                raise ValueError("output must be a bounded readable regular inode")
            content = bytearray()
            while len(content) <= cap:
                chunk = os.read(fd, min(65536, cap + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(fd)

            def identity(value):
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_uid,
                    value.st_gid,
                    value.st_nlink,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )

            if (
                len(content) != before.st_size
                or len(content) > cap
                or identity(before) != identity(after)
            ):
                raise ValueError("output changed or exceeded its bound")
            return bytes(content)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def frame(kind: int, payload: bytes) -> bytes:
    if (
        type(kind) is not int
        or kind not in (2, 3)
        or type(payload) is not bytes
        or len(payload) > MAX_FRAME
    ):
        raise ValueError("invalid protected frame")
    return MAGIC + bytes([kind]) + struct.pack(">I", len(payload)) + payload


def parse_frame(raw: bytes) -> tuple[int, bytes]:
    if type(raw) is not bytes or len(raw) < 11 or raw[:6] != MAGIC:
        raise ValueError("missing or truncated protected frame")
    kind, size = raw[6], struct.unpack(">I", raw[7:11])[0]
    if kind not in (2, 3) or size > MAX_FRAME or len(raw) != 11 + size:
        raise ValueError("invalid, truncated, duplicate or oversized protected frame")
    return kind, raw[11:]


def write_frame(kind: int, payload: bytes) -> None:
    data = frame(kind, payload)
    offset = 0
    while offset < len(data):
        written = os.write(1, data[offset : offset + 65536])
        if written <= 0:
            raise OSError("short export write")
        offset += written


def export_output(name: str, child_exit_code: int) -> None:
    if name not in OUTPUTS or type(child_exit_code) is not int:
        raise ValueError("fixed numeric export required")
    try:
        content = read_regular(name, MAX_OUTPUT)
    except (OSError, ValueError):
        write_frame(3, rejection_metadata(name, child_exit_code))
        return
    metadata = json.dumps(
        {
            "schema": "mineagent.numeric-quiescence.v2",
            "basename": name,
            "child_exit_code": child_exit_code,
            "descendants_reaped": True,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    write_frame(2, struct.pack(">I", len(metadata)) + metadata + content)


def validate_export(payload: bytes, *, expected_basename: str) -> bytes:
    if expected_basename not in OUTPUTS:
        raise ValueError("fixed numeric basename required")
    if type(payload) is not bytes or not 4 <= len(payload) <= MAX_FRAME:
        raise ValueError("truncated export metadata")
    size = struct.unpack(">I", payload[:4])[0]
    if not 0 < size <= 512 or len(payload) < 4 + size:
        raise ValueError("invalid export metadata bound")
    metadata = json.loads(payload[4 : 4 + size])
    if (
        type(metadata) is not dict
        or json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("ascii")
        != payload[4 : 4 + size]
    ):
        raise ValueError("noncanonical metadata")
    content = payload[4 + size :]
    if not 0 < len(content) <= MAX_OUTPUT or metadata != {
        "schema": "mineagent.numeric-quiescence.v2",
        "basename": expected_basename,
        "child_exit_code": 0,
        "descendants_reaped": True,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }:
        raise ValueError("export metadata mismatch")
    if (
        type(metadata["child_exit_code"]) is not int
        or type(metadata["size"]) is not int
        or metadata["descendants_reaped"] is not True
    ):
        raise ValueError("noncanonical export metadata types")
    return content


def rejection_metadata(name: str, child_exit_code: int) -> bytes:
    if (
        name not in OUTPUTS
        or type(child_exit_code) is not int
        or not -64 <= child_exit_code <= 255
    ):
        raise ValueError("invalid protected rejection identity")
    return json.dumps(
        {
            "schema": "mineagent.numeric-rejection.v1",
            "basename": name,
            "child_exit_code": child_exit_code,
            "descendants_reaped": True,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def validate_rejection(
    payload: bytes, *, expected_basename: str, expected_exit_code: int
) -> int:
    if payload != rejection_metadata(expected_basename, expected_exit_code):
        raise ValueError("protected rejection did not match exact child exit")
    return expected_exit_code
