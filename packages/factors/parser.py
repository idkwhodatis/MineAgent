"""Hostile Parquet verifier ENTRYPOINT for an externally isolated container.

This is not an isolation launcher. The caller MUST supply no-network/nonroot,
credential-free, read-only-rootfs containment with 512MiB memory, bounded PIDs,
10-second startup+wallclock limit and size-bounded output mount. Never invoke
parse_partition inside the API/orchestrator process. Only fixed input RO and
output RW mounts are exposed, never supervisor journals or publication paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import stat
import sys
from pathlib import Path

from .storage import (
    COLUMN_SQL,
    COLUMNS,
    MAX_ROWS,
    FactorStorageError,
    _connection,
    validate_rows,
    verified_file,
    write_partition,
)

MAX_INPUT_BYTES = 10_000_000
MAX_REPORT_BYTES = 16_384
REPORT_SCHEMA = "mineagent.factor-parser-report.v1"
OUTPUT_NAME = "values.parquet"


def read_identity_map(path):
    """Closed, bounded host-authored identity transform; never source authority."""
    from uuid import UUID

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024:
            raise FactorStorageError("identity_map")
        raw = os.read(fd, 1025)

        def pairs(items):
            result = {}
            for k, v in items:
                if k in result:
                    raise ValueError()
                result[k] = v
            return result

        mapping = json.loads(raw, object_pairs_hook=pairs)
        if (
            set(mapping)
            != {"schema_version", "source_factor_version_id", "local_factor_version_id"}
            or mapping["schema_version"] != "mineagent.factor-identity-map.v1"
        ):
            raise ValueError()
        for key in ("source_factor_version_id", "local_factor_version_id"):
            if str(UUID(mapping[key])) != mapping[key]:
                raise ValueError()
        return mapping, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        raise FactorStorageError("identity_map") from None
    finally:
        os.close(fd)


def parse_partition(
    input_path: Path,
    output_directory: Path,
    *,
    identity_map_path=None,
    snapshot_input_path=None,
) -> dict:
    """Only call INSIDE containment. Return bounded derived-output provenance."""
    input_path, output_directory = Path(input_path), Path(output_directory)
    fd = os.open(input_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not 12 <= info.st_size <= MAX_INPUT_BYTES
        ):
            raise FactorStorageError("input_limit")
        raw = b""
        while chunk := os.read(fd, min(1024 * 1024, MAX_INPUT_BYTES + 1 - len(raw))):
            raw += chunk
            if len(raw) > MAX_INPUT_BYTES:
                raise FactorStorageError("input_limit")
        if raw[:4] != b"PAR1" or raw[-4:] != b"PAR1":
            raise FactorStorageError("parquet_header")
        footer = int.from_bytes(raw[-8:-4], "little")
        if footer > 1_048_576 or footer > len(raw) - 12:
            raise FactorStorageError("parquet_footer")
        input_digest = hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)
    if output_directory.is_symlink() or not output_directory.is_dir():
        raise FactorStorageError("output_directory")
    with (
        verified_file(input_path, input_digest, max_bytes=MAX_INPUT_BYTES) as source,
        _connection(seconds=8) as con,
    ):
        schema = con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [source]
        ).fetchall()
        if len(schema) != len(COLUMNS) or {r[0]: r[1] for r in schema} != COLUMNS:
            raise FactorStorageError("row_schema")
        metadata = con.execute(
            "SELECT count(DISTINCT row_group_id), max(row_group_num_rows) FROM parquet_metadata(?)",
            [source],
        ).fetchone()
        if metadata[0] > 1024 or (metadata[1] or 0) > MAX_ROWS:
            raise FactorStorageError("row_groups")
        cursor = con.execute(
            "SELECT " + COLUMN_SQL + " FROM read_parquet(?) LIMIT ?",
            [source, MAX_ROWS + 1],
        )
        rows = []
        while batch := cursor.fetchmany(1024):
            if len(rows) + len(batch) > MAX_ROWS:
                raise FactorStorageError("row_limit")
            rows.extend(dict(zip(COLUMNS, values, strict=True)) for values in batch)
        rows = validate_rows(rows)
    if len({r["factor_version_id"] for r in rows}) > 1:
        raise FactorStorageError("multiple_versions")
    from .contracts import FeatureRow, SnapshotInput, rows_digest
    from .contracts import validate_rows as validate_contract_rows

    source_rows_sha256 = rows_digest(tuple(FeatureRow.model_validate(r) for r in rows))
    source_ids = sorted({r["factor_version_id"] for r in rows})
    mapping_digest = None
    if identity_map_path is not None:
        mapping, mapping_digest = read_identity_map(identity_map_path)
        if any(
            r["factor_version_id"] != mapping["source_factor_version_id"] for r in rows
        ):
            raise FactorStorageError("identity_map_source")
        rows = [
            {**r, "factor_version_id": mapping["local_factor_version_id"]} for r in rows
        ]
    contract_digest = None
    if snapshot_input_path is not None:
        fd = os.open(snapshot_input_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 1_048_576
            ):
                raise FactorStorageError("snapshot_input")
            contract_raw = os.read(fd, 1_048_577)
            contract = SnapshotInput.model_validate_json(contract_raw)
            contract_digest = hashlib.sha256(contract_raw).hexdigest()
        finally:
            os.close(fd)
        validate_contract_rows(
            contract, tuple(FeatureRow.model_validate(r) for r in rows)
        )
    semantic_digest = rows_digest(tuple(FeatureRow.model_validate(r) for r in rows))
    partition = write_partition(rows, output_directory / OUTPUT_NAME)
    report = {
        "schema_version": REPORT_SCHEMA,
        "status": "validated",
        "input_sha256": input_digest,
        "input_size_bytes": len(raw),
        "output_name": OUTPUT_NAME,
        "output_sha256": partition.sha256,
        "output_size_bytes": partition.size_bytes,
        "row_count": partition.row_count,
        "valid_count": partition.valid_count,
        "missing_count": partition.missing_count,
        "identity_mode": "derived",
        "source_bytes_preserved": True,
        "temporal_verification": "declared_or_unknown",
        "factor_version_ids": sorted({r["factor_version_id"] for r in rows}),
        "source_factor_version_ids": source_ids,
        "identity_mapping_sha256": mapping_digest,
        "snapshot_input_sha256": contract_digest,
        "rows_sha256": semantic_digest,
        "source_rows_sha256": source_rows_sha256,
    }
    if len(report["factor_version_ids"]) > 1:
        raise FactorStorageError("multiple_versions")
    validate_report(report)
    return report


def validate_report(report: dict) -> dict:
    fields = {
        "schema_version",
        "status",
        "input_sha256",
        "input_size_bytes",
        "output_name",
        "output_sha256",
        "output_size_bytes",
        "row_count",
        "valid_count",
        "missing_count",
        "identity_mode",
        "source_bytes_preserved",
        "temporal_verification",
        "factor_version_ids",
        "source_factor_version_ids",
        "identity_mapping_sha256",
        "snapshot_input_sha256",
        "rows_sha256",
        "source_rows_sha256",
    }
    if not isinstance(report, dict) or set(report) != fields:
        raise FactorStorageError("parser_report")
    if (
        report["schema_version"] != REPORT_SCHEMA
        or report["status"] != "validated"
        or report["output_name"] != OUTPUT_NAME
        or report["identity_mode"] != "derived"
        or report["source_bytes_preserved"] is not True
        or report["temporal_verification"] != "declared_or_unknown"
    ):
        raise FactorStorageError("parser_report")
    for field in ("input_sha256", "output_sha256", "rows_sha256", "source_rows_sha256"):
        value = report[field]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise FactorStorageError("parser_report")
    for field, limit in (
        ("input_size_bytes", MAX_INPUT_BYTES),
        ("output_size_bytes", 256 * 1024 * 1024),
        ("row_count", MAX_ROWS),
        ("valid_count", MAX_ROWS),
        ("missing_count", MAX_ROWS),
    ):
        if type(report[field]) is not int or not 0 <= report[field] <= limit:
            raise FactorStorageError("parser_report")
    if report["valid_count"] + report["missing_count"] != report["row_count"]:
        raise FactorStorageError("parser_report")
    contract_digest = report["snapshot_input_sha256"]
    if contract_digest is not None and (
        not isinstance(contract_digest, str)
        or len(contract_digest) != 64
        or any(c not in "0123456789abcdef" for c in contract_digest)
    ):
        raise FactorStorageError("parser_report")
    mapping_digest = report["identity_mapping_sha256"]
    if mapping_digest is not None and (
        not isinstance(mapping_digest, str)
        or len(mapping_digest) != 64
        or any(c not in "0123456789abcdef" for c in mapping_digest)
    ):
        raise FactorStorageError("parser_report")
    from uuid import UUID

    ids = report["factor_version_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) > 1
        or (report["row_count"] > 0 and len(ids) != 1)
    ):
        raise FactorStorageError("parser_report")
    try:
        source_ids = report["source_factor_version_ids"]
        if not isinstance(source_ids, list) or len(source_ids) != len(ids):
            raise ValueError()
        if mapping_digest is None and source_ids != ids:
            raise ValueError()
        if any(
            not isinstance(v, str) or str(UUID(v)) != v for v in [*ids, *source_ids]
        ):
            raise ValueError()
        if len(json.dumps(report, allow_nan=False).encode()) > MAX_REPORT_BYTES:
            raise ValueError()
    except (ValueError, TypeError):
        raise FactorStorageError("parser_report") from None
    return dict(report)


def main():
    # Additional limits, NOT a substitute for supervisor cgroup/network isolation.
    os.environ.clear()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (9, 9))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        if len(sys.argv) != 1:
            raise FactorStorageError("arguments")
        mapping = Path("/input/identity-map.json")
        result = parse_partition(
            Path("/input/input.parquet"),
            Path("/output"),
            identity_map_path=mapping if mapping.exists() else None,
            snapshot_input_path=Path("/input/snapshot-input.json")
            if Path("/input/snapshot-input.json").exists()
            else None,
        )
        sys.stdout.write(
            json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        )
        sys.stdout.flush()
        with open("/output/values.parquet", "rb") as output:
            while chunk := output.read(65536):
                sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        return 0
    except Exception:  # noqa: BLE001 -- never expose native parser/path diagnostics
        sys.stdout.write(
            '{"schema_version":"mineagent.factor-parser-report.v1","status":"rejected","code":"invalid_partition"}\n'
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
