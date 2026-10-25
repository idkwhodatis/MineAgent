"""Deterministic bounded factor archives. This module never parses tabular bytes."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass

MAX_ARCHIVE_BYTES = 10_000_000
MAX_MANIFEST_BYTES = 1_048_576
MAX_ENTRIES = 128
SCHEMA = "mineagent.factor-package.v1"
_PATH = re.compile(r"objects/[a-f0-9]{64}\.(?:parquet|json|py|bin)\Z")


class FactorPackageError(ValueError):
    def __init__(self, code: str = "invalid_factor_package"):
        self.code = code
        super().__init__(code)


def _canonical(value):
    try:
        result = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise FactorPackageError() from None
    if len(result) > MAX_MANIFEST_BYTES:
        raise FactorPackageError("manifest_size")
    return result


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise FactorPackageError("duplicate_json_key")
        result[key] = value
    return result


def _json(raw):
    if len(raw) > MAX_MANIFEST_BYTES:
        raise FactorPackageError("manifest_size")
    try:
        result = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(FactorPackageError()),
        )
        # Iterative depth/element bound before further traversal.
        pending, count = [(result, 0)], 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if depth > 24 or count > 20_000:
                raise FactorPackageError("manifest_complexity")
            if isinstance(value, dict):
                pending.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, list):
                pending.extend((v, depth + 1) for v in value)
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise FactorPackageError() from None


@dataclass(frozen=True)
class FactorPackage:
    manifest: dict
    objects: dict[str, bytes]
    archive_sha256: str


def encode_package(manifest: dict, objects: Mapping[str, bytes]) -> bytes:
    """Wrap domain metadata as payload; compute the complete physical inventory."""
    if not isinstance(manifest, dict) or len(objects) > MAX_ENTRIES - 1:
        raise FactorPackageError()
    inventory = []
    total = 0
    for path, content in sorted(objects.items()):
        if (
            not isinstance(path, str)
            or not _PATH.fullmatch(path)
            or not isinstance(content, bytes)
        ):
            raise FactorPackageError()
        if (path.endswith(".py") and len(content) > 128 * 1024) or (
            path.endswith(".json") and len(content) > MAX_MANIFEST_BYTES
        ):
            raise FactorPackageError("object_size")
        digest = hashlib.sha256(content).hexdigest()
        if path.split("/")[1].split(".")[0] != digest:
            raise FactorPackageError("object_hash")
        total += len(content)
        if total > MAX_ARCHIVE_BYTES:
            raise FactorPackageError("archive_size")
        inventory.append({"path": path, "sha256": digest, "size_bytes": len(content)})
    metadata = _canonical(
        {"schema_version": SCHEMA, "payload": manifest, "inventory": inventory}
    )
    _json(metadata)
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_STORED, allowZip64=False
    ) as archive:
        for path, content in [("manifest.json", metadata), *sorted(objects.items())]:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, content)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise FactorPackageError("archive_size")
    return result


def decode_package(payload: bytes) -> FactorPackage:
    """Validate archive identity and bytes without extraction or native parsing."""
    if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE_BYTES:
        raise FactorPackageError("archive_size")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_ENTRIES or archive.comment:
                raise FactorPackageError()
            names = [info.filename for info in entries]
            if len(set(names)) != len(names) or names.count("manifest.json") != 1:
                raise FactorPackageError("archive_inventory")
            total = 0
            for info in entries:
                mode = info.external_attr >> 16
                if (
                    info.filename != "manifest.json"
                    and not _PATH.fullmatch(info.filename)
                ) or info.is_dir():
                    raise FactorPackageError("archive_path")
                if (
                    info.compress_type != zipfile.ZIP_STORED
                    or info.compress_size != info.file_size
                    or info.flag_bits & 1
                    or info.extra
                    or info.comment
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                ):
                    raise FactorPackageError("archive_entry")
                total += info.file_size
                if info.file_size < 0 or total > MAX_ARCHIVE_BYTES:
                    raise FactorPackageError("archive_size")
            manifest = _json(archive.read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or set(manifest) != {"schema_version", "payload", "inventory"}
                or manifest["schema_version"] != SCHEMA
                or not isinstance(manifest["payload"], dict)
            ):
                raise FactorPackageError("manifest_schema")
            inventory = manifest["inventory"]
            if not isinstance(inventory, list) or len(inventory) != len(entries) - 1:
                raise FactorPackageError("archive_inventory")
            objects = {}
            for item in inventory:
                if not isinstance(item, dict) or set(item) != {
                    "path",
                    "sha256",
                    "size_bytes",
                }:
                    raise FactorPackageError("archive_inventory")
                path = item["path"]
                if (
                    not isinstance(path, str)
                    or not _PATH.fullmatch(path)
                    or path in objects
                    or path not in names
                ):
                    raise FactorPackageError("archive_inventory")
                content = archive.read(path)
                digest = hashlib.sha256(content).hexdigest()
                if (
                    type(item["size_bytes"]) is not int
                    or item["size_bytes"] != len(content)
                    or item["sha256"] != digest
                    or path.split("/")[1].split(".")[0] != digest
                ):
                    raise FactorPackageError("object_hash")
                objects[path] = content
            # The v1 transport is canonical, not arbitrary ZIP: reject hidden local
            # entries, prepended/trailing bytes, inconsistent headers and extras.
            if encode_package(manifest["payload"], objects) != payload:
                raise FactorPackageError("noncanonical_archive")
            return FactorPackage(
                manifest["payload"], objects, hashlib.sha256(payload).hexdigest()
            )
    except FactorPackageError:
        raise
    except (
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        TypeError,
        UnicodeError,
        RuntimeError,
        NotImplementedError,
    ):
        raise FactorPackageError() from None
