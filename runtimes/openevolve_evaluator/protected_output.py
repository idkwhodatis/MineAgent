"""MAOE1 protected native observation export. Stdlib only; no native imports.

This establishes bounded bytes from a quiesced unit, not financial authenticity.
Only the fixed protected post-ECHILD helper may export these paths.
"""

import hashlib
import json
import os
import stat
import struct

MAGIC = b"MAOE1\0"
MAX_FRAME = 16 * 1024**2
MAX_TRANSCRIPT = 8 * 1024**2
MAX_METRICS = 1024**2
NAMES = ("metrics.json", "stderr.bin", "stdout.bin")


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _read(directory, name, *, owner, mode, cap, allow_empty=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or not (0 if allow_empty else 1) <= before.st_size <= cap
        ):
            raise ValueError("Invalid native protected inode")
        raw = bytearray()
        while len(raw) <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        keys = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(raw) != before.st_size
            or len(raw) > cap
            or any(getattr(before, k) != getattr(after, k) for k in keys)
        ):
            raise ValueError("Native protected inode changed")
        return bytes(raw)
    finally:
        os.close(fd)


def read_fixed_outputs():
    output = os.open("/output", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    status = os.open("/status", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for directory, mode in ((output, 0o733), (status, 0o700)):
            info = os.fstat(directory)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode:
                raise ValueError("Native protected namespace differs")
        if set(os.listdir(output)) != {"metrics.json"}:
            raise ValueError("Unexpected native artifact")
        files = {
            "metrics.json": _read(
                output, "metrics.json", owner=65533, mode=0o644, cap=MAX_METRICS
            )
        }
        for name in ("stdout.bin", "stderr.bin"):
            files[name] = _read(
                status,
                name,
                owner=0,
                mode=0o600,
                cap=MAX_TRANSCRIPT,
                allow_empty=name == "stderr.bin",
            )
        if len(files["stdout.bin"]) + len(files["stderr.bin"]) > MAX_TRANSCRIPT:
            raise ValueError("Native transcript exceeds combined bound")
        return files
    finally:
        os.close(status)
        os.close(output)


def export_payload(files, child_exit_code):
    if (
        type(files) is not dict
        or set(files) != set(NAMES)
        or type(child_exit_code) is not int
        or child_exit_code != 0
        or any(type(raw) is not bytes for raw in files.values())
    ):
        raise ValueError("Successful fixed native export required")
    if (
        not 0 < len(files["metrics.json"]) <= MAX_METRICS
        or not files["stdout.bin"]
        or len(files["stdout.bin"]) + len(files["stderr.bin"]) > MAX_TRANSCRIPT
    ):
        raise ValueError("Native export exceeds profile")
    metadata = {
        "schema": "mineagent.openevolve-native-quiescence.v1",
        "child_exit_code": 0,
        "descendants_reaped": True,
        "qualification": "native_observation_not_producer_proof",
        "files": [
            {
                "path": name,
                "bytes": len(files[name]),
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name in NAMES
        ],
    }
    raw = _canonical(metadata)
    return struct.pack(">I", len(raw)) + raw + b"".join(files[name] for name in NAMES)


def validate_export(payload):
    if type(payload) is not bytes or not 4 < len(payload) <= MAX_FRAME:
        raise ValueError("Invalid native export size")
    size = struct.unpack(">I", payload[:4])[0]
    if not 0 < size <= 4096 or 4 + size >= len(payload):
        raise ValueError("Invalid native export metadata")
    metadata = json.loads(payload[4 : 4 + size])
    if (
        _canonical(metadata) != payload[4 : 4 + size]
        or type(metadata) is not dict
        or set(metadata)
        != {"schema", "child_exit_code", "descendants_reaped", "qualification", "files"}
        or metadata["schema"] != "mineagent.openevolve-native-quiescence.v1"
        or type(metadata["child_exit_code"]) is not int
        or metadata["child_exit_code"] != 0
        or metadata["descendants_reaped"] is not True
        or metadata["qualification"] != "native_observation_not_producer_proof"
        or type(metadata["files"]) is not list
        or len(metadata["files"]) != 3
    ):
        raise ValueError("Invalid native protected metadata")
    offset = 4 + size
    files = {}
    for name, record in zip(NAMES, metadata["files"], strict=True):
        if (
            type(record) is not dict
            or set(record) != {"path", "bytes", "sha256"}
            or record["path"] != name
            or type(record["bytes"]) is not int
            or not 0 <= record["bytes"] <= MAX_TRANSCRIPT
        ):
            raise ValueError("Invalid native artifact inventory")
        raw = payload[offset : offset + record["bytes"]]
        offset += record["bytes"]
        if (
            len(raw) != record["bytes"]
            or hashlib.sha256(raw).hexdigest() != record["sha256"]
        ):
            raise ValueError("Native artifact hash differs")
        files[name] = raw
    if offset != len(payload) or export_payload(files, 0) != payload:
        raise ValueError("Trailing or noncanonical native export")
    return files


def frame(kind, payload):
    if (
        type(kind) is not int
        or kind not in (2, 3)
        or type(payload) is not bytes
        or len(payload) > MAX_FRAME
    ):
        raise ValueError("Invalid MAOE1 frame")
    return MAGIC + bytes([kind]) + struct.pack(">I", len(payload)) + payload


def parse_frame(raw):
    if type(raw) is not bytes or len(raw) < 11 or raw[:6] != MAGIC:
        raise ValueError("Missing MAOE1 frame")
    kind, size = raw[6], struct.unpack(">I", raw[7:11])[0]
    if kind not in (2, 3) or size > MAX_FRAME or len(raw) != 11 + size:
        raise ValueError("Invalid truncated or duplicate MAOE1 frame")
    return kind, raw[11:]


def export_output(child_exit_code):
    try:
        payload = export_payload(read_fixed_outputs(), child_exit_code)
        data = frame(2, payload)
    except (OSError, ValueError):
        data = frame(
            3,
            _canonical(
                {
                    "schema": "mineagent.openevolve-native-rejection.v1",
                    "descendants_reaped": True,
                    "child_exit_code": child_exit_code,
                }
            ),
        )
    offset = 0
    while offset < len(data):
        size = os.write(1, data[offset : offset + 65536])
        if size <= 0:
            raise OSError("Short protected export")
        offset += size
