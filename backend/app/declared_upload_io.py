"""Bounded immutable dataset reads for the explicit declared-upload profile."""

import hashlib
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from backend.app.core.atomic_storage import _open_directory

MAX_BYTES = 64 * 1024**2
MAX_FILES = 1024


def safe_artifact_name(name):
    if (
        type(name) is not str
        or not 1 <= len(name) <= 240
        or "\\" in name
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or len(name.split("/")) > 6
        or any(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None for part in name.split("/")
        )
    ):
        raise ValueError("Invalid declared-upload artifact identity")
    return PurePosixPath(name)


def read_artifact(root, storage_key, name, expected_hash, maximum):
    if (
        type(storage_key) is not str
        or re.fullmatch(r"datasets/[a-f0-9-]{36}/[a-f0-9-]{36}", storage_key) is None
        or type(expected_hash) is not str
        or re.fullmatch("[a-f0-9]{64}", expected_hash) is None
        or type(maximum) is not int
        or not 0 < maximum <= MAX_BYTES
    ):
        raise ValueError("Exact bounded dataset artifact required")
    relative = safe_artifact_name(name)
    deadline = time.monotonic() + 10
    parent = _open_directory(Path(root).absolute() / storage_key / relative.parent)
    try:
        fd = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or not 0 < before.st_size <= maximum
            ):
                raise ValueError("Bounded single-link regular artifact required")
            result = bytearray()
            while chunk := os.read(fd, min(65536, maximum + 1 - len(result))):
                if time.monotonic() >= deadline:
                    raise ValueError("Artifact read deadline")
                result.extend(chunk)
                if len(result) > maximum:
                    raise ValueError("Artifact bound exceeded")
            identity = lambda x: (
                x.st_dev,
                x.st_ino,
                x.st_mode,
                x.st_nlink,
                x.st_size,
                x.st_mtime_ns,
                x.st_ctime_ns,
            )
            if (
                identity(before) != identity(os.fstat(fd))
                or identity(before)
                != identity(
                    os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                )
                or len(result) != before.st_size
                or hashlib.sha256(result).hexdigest() != expected_hash
            ):
                raise ValueError("Retained artifact identity changed")
            return bytes(result)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def artifact_inventory(manifest):
    result = {}

    def add(name, value):
        safe_artifact_name(name)
        if (
            name == "manifest.json"
            or type(value) is not str
            or re.fullmatch("[a-f0-9]{64}", value) is None
            or name in result
            and result[name] != value
        ):
            raise ValueError("Conflicting artifact inventory")
        result[name] = value
        if len(result) > MAX_FILES:
            raise ValueError("Artifact count exceeded")

    for field in ("files", "artifact_files"):
        values = manifest.get(field, {})
        if type(values) is not dict:
            raise ValueError("Exact artifact inventory required")
        for name, value in values.items():
            add(name, value)
    previews = manifest.get("preview_files", {})
    if type(previews) is not dict:
        raise ValueError("Exact preview inventory required")
    for entries in previews.values():
        if type(entries) is not list:
            raise ValueError("Exact preview inventory required")
        for entry in entries:
            if type(entry) is not dict:
                raise ValueError("Exact preview identity required")
            add(entry["path"], entry["sha256"])
    return result


def read_dataset_bytes(root, row):
    from packages.strategy_sdk import JsonLimits, parse_json

    raw = read_artifact(
        root, row.storage_key, "manifest.json", row.content_sha256, 2 * 1024**2
    )
    manifest = parse_json(
        raw, limits=JsonLimits(max_bytes=2 * 1024**2, max_nodes=100000, max_items=10000)
    )
    if manifest != row.manifest:
        raise ValueError("Database and retained manifest differ")
    artifacts = {}
    total = len(raw)
    deadline = time.monotonic() + 10
    for name, sha in sorted(artifact_inventory(manifest).items()):
        if total >= MAX_BYTES or time.monotonic() >= deadline:
            raise ValueError("Total retained dataset exceeds bound")
        value = read_artifact(root, row.storage_key, name, sha, MAX_BYTES - total)
        total += len(value)
        artifacts[name] = value
    return raw, artifacts
