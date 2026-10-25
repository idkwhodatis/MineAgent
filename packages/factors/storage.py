"""Bounded canonical factor partitions, for trusted rows and admitted bytes only.

Public uploads MUST first pass the external isolated parser supervisor. Calling
these routines is not a sandbox and never supplies provenance/PIT attestation.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

MAX_ROWS = 1_000_000
MAX_SCAN_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_ROW_BYTES = 4096
MAX_PAGE_ROWS = 500
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
COLUMNS = {
    "event_time": "TIMESTAMP WITH TIME ZONE",
    "session_date": "DATE",
    "listing_id": "VARCHAR",
    "factor_version_id": "VARCHAR",
    "available_at": "TIMESTAMP WITH TIME ZONE",
    "value": "DOUBLE",
    "value_state": "VARCHAR",
    "missing_reason": "VARCHAR",
    "input_window_start": "DATE",
    "input_window_end": "DATE",
    "input_evidence_digest": "VARCHAR",
    "required_input_available_at": "TIMESTAMP WITH TIME ZONE",
    "membership_available_at": "TIMESTAMP WITH TIME ZONE",
    "lag_available_at": "TIMESTAMP WITH TIME ZONE",
}
COLUMN_SQL = ", ".join(
    f"CAST({name} AS VARCHAR) AS {name}" if kind == "TIMESTAMP WITH TIME ZONE" else name
    for name, kind in COLUMNS.items()
)


class FactorStorageError(ValueError):
    def __init__(self, code="factor_integrity"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Partition:
    path: Path
    sha256: str
    size_bytes: int
    row_count: int
    valid_count: int
    missing_count: int


def _timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise FactorStorageError("timestamp")
    return value.astimezone(UTC)


def _date(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if type(value) is not date:
        raise FactorStorageError("date")
    return value


def validate_row(row):
    """Validate representation, not the truth of source availability claims."""
    from .contracts import FeatureRow

    try:
        if hasattr(row, "model_dump"):
            row = row.model_dump(mode="python")
        if not isinstance(row, dict) or set(row) != set(COLUMNS):
            raise FactorStorageError("row_schema")
        row = dict(row)
        for name, kind in COLUMNS.items():
            if kind == "TIMESTAMP WITH TIME ZONE":
                row[name] = _timestamp(row[name])
        result = FeatureRow.model_validate(row).model_dump(mode="python")
        result["factor_version_id"] = str(result["factor_version_id"])
        return result
    except (ValueError, TypeError, OverflowError):
        raise FactorStorageError("row_schema") from None


def validate_rows(rows):
    seen, result = set(), []
    for row in rows:
        if len(result) >= MAX_ROWS:
            raise FactorStorageError("row_limit")
        row = validate_row(row)
        key = (row["event_time"], row["listing_id"], row["factor_version_id"])
        if key in seen:
            raise FactorStorageError("duplicate_row")
        seen.add(key)
        result.append(row)
    return sorted(
        result, key=lambda r: (r["event_time"], r["listing_id"], r["factor_version_id"])
    )


@contextmanager
def _connection(seconds=30):
    import duckdb

    connection = duckdb.connect(
        config={
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "threads": "1",
            "memory_limit": "256MB",
            "max_temp_directory_size": "0B",
            "enable_external_access": "true",
        }
    )
    timer = threading.Timer(seconds, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        yield connection
    finally:
        timer.cancel()
        connection.close()


@contextmanager
def verified_file(path, expected_sha256, *, max_bytes=MAX_SCAN_BYTES):
    """Pin a no-follow regular descriptor, verify bytes, and detect mutation."""
    path = Path(path)
    absolute = path.absolute()
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:-1]:
            next_directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = next_directory
        fd = os.open(
            absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 <= before.st_size <= max_bytes
        ):
            raise FactorStorageError("unsafe_file")
        digest = hashlib.sha256()
        consumed = 0
        while chunk := os.read(fd, 1024 * 1024):
            consumed += len(chunk)
            if consumed > max_bytes or consumed > before.st_size:
                raise FactorStorageError("file_changed")
            digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise FactorStorageError("object_hash")
        os.lseek(fd, 0, os.SEEK_SET)
        yield f"/proc/self/fd/{fd}"
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise FactorStorageError("file_changed")
    finally:
        os.close(fd)


def write_partition(rows, path: Path, *, seconds=20) -> Partition:
    """Write trusted validated rows into supervisor-owned charged staging only."""
    deadline = time.monotonic() + seconds
    if not 0 < seconds <= 30:
        raise FactorStorageError("deadline")
    rows = validate_rows(rows)
    if time.monotonic() >= deadline:
        raise FactorStorageError("deadline")
    path = Path(path)
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise FactorStorageError("output_exists")
    fd, temporary = tempfile.mkstemp(
        prefix=".factor-", suffix=".parquet", dir=path.parent
    )
    os.close(fd)
    try:
        with _connection(seconds=max(0.001, deadline - time.monotonic())) as con:
            con.execute(
                "CREATE TABLE factor_values ("
                + ", ".join(f"{k} {v}" for k, v in COLUMNS.items())
                + ")"
            )
            if rows:
                con.executemany(
                    "INSERT INTO factor_values VALUES ("
                    + ",".join("?" for _ in COLUMNS)
                    + ")",
                    [tuple(row[k] for k in COLUMNS) for row in rows],
                )
            con.execute(
                "COPY (SELECT * FROM factor_values ORDER BY event_time,listing_id,factor_version_id) TO ? (FORMAT PARQUET, COMPRESSION UNCOMPRESSED)",
                [temporary],
            )
        size = os.stat(temporary).st_size
        if size > MAX_OUTPUT_BYTES:
            raise FactorStorageError("output_limit")
        with open(temporary, "rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            os.fsync(stream.fileno())
        if time.monotonic() >= deadline:
            raise FactorStorageError("deadline")
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        valid = sum(row["value_state"] == "valid" for row in rows)
        return Partition(path, digest, size, len(rows), valid, len(rows) - valid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_values_page(
    path,
    expected_sha256,
    *,
    offset=0,
    limit=100,
    listing_id=None,
    start=None,
    end=None,
    seconds=30,
):
    """Exact filtered count and page; session window is half-open [start,end)."""
    if (
        type(offset) is not int
        or not 0 <= offset <= MAX_ROWS
        or type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_ROWS
    ):
        raise FactorStorageError("page_bounds")
    if listing_id is not None and (
        not isinstance(listing_id, str) or len(listing_id.encode()) > 256
    ):
        raise FactorStorageError("listing_id")
    if not 0 < seconds <= 30:
        raise FactorStorageError("deadline")
    try:
        start = _date(start) if start is not None else None
        end = _date(end) if end is not None else None
        if start is not None and end is not None and start >= end:
            raise FactorStorageError("window")
        deadline = time.monotonic() + seconds
        with verified_file(path, expected_sha256) as source:
            if time.monotonic() >= deadline:
                raise FactorStorageError("deadline")
            with _connection(seconds=deadline - time.monotonic()) as con:
                schema = con.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [source]
                ).fetchall()
                if (
                    len(schema) != len(COLUMNS)
                    or {r[0]: r[1] for r in schema} != COLUMNS
                ):
                    raise FactorStorageError("row_schema")
                predicate = " FROM read_parquet(?) WHERE (? IS NULL OR listing_id=?) AND (? IS NULL OR session_date>=?) AND (? IS NULL OR session_date<?)"
                params = [source, listing_id, listing_id, start, start, end, end]
                count = con.execute("SELECT count(*)" + predicate, params).fetchone()[0]
                if count > MAX_ROWS:
                    raise FactorStorageError("row_limit")
                rows = con.execute(
                    "SELECT "
                    + COLUMN_SQL
                    + predicate
                    + " ORDER BY event_time, listing_id, factor_version_id LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
                result = [
                    validate_row(dict(zip(COLUMNS, row, strict=True))) for row in rows
                ]
                encoded = json.dumps(result, default=str, allow_nan=False).encode()
                if len(encoded) > MAX_RESPONSE_BYTES:
                    raise FactorStorageError("response_limit")
                if time.monotonic() >= deadline:
                    raise FactorStorageError("deadline")
                return {"data": result, "count": count}
    except FactorStorageError:
        raise
    except Exception:  # noqa: BLE001 -- never expose native parser/path diagnostics
        raise FactorStorageError("partition_read") from None


def read_page(path, expected_sha256, **kwargs):
    """Compatibility convenience for consumers needing only a page of rows."""
    return read_values_page(path, expected_sha256, **kwargs)["data"]
