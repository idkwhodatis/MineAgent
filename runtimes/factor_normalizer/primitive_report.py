"""Bounded canonical primitive output; never a host HDF/Parquet parser.

A report alone is not authority. Consumers must resolve its protected extraction,
whole-unit stop and persisted input/source/native-consumer graph first.
"""

import hashlib
import json
import math
import re

SCHEMA = "mineagent.factor-normalized-primitives.v1"
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 10000
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def validate_report(raw, *, parquet, manifest, listings):
    """Validate primitive bytes against the exact retained opaque/input bytes."""
    if type(parquet) is not bytes:
        raise ValueError("report requires exact opaque bytes")
    return validate_bound_report(
        raw,
        parquet_sha256=hashlib.sha256(parquet).hexdigest(),
        parquet_size_bytes=len(parquet),
        manifest=manifest,
        listings=listings,
    )


def validate_bound_report(
    raw, *, parquet_sha256, parquet_size_bytes, manifest, listings
):
    """Grammar-only backup helper; caller separately verifies actual file digest."""
    if any(type(x) is not bytes for x in (raw, manifest, listings)):
        raise ValueError("report requires exact bytes")
    if (
        type(parquet_sha256) is not str
        or not _SHA.fullmatch(parquet_sha256)
        or type(parquet_size_bytes) is not int
        or parquet_size_bytes <= 0
        or not raw
        or len(raw) + parquet_size_bytes > MAX_BYTES
    ):
        raise ValueError("combined normalized output exceeds bound")
    if not 0 < len(manifest) <= 65536 or not 0 < len(listings) <= 1024 * 1024:
        raise ValueError("normalizer input metadata bound")
    try:
        document = json.loads(raw)
        input_manifest = json.loads(manifest)
        listing_rows = json.loads(listings)
        if (
            canonical(document) != raw
            or canonical(input_manifest) != manifest
            or canonical(listing_rows) != listings
        ):
            raise ValueError("noncanonical report or metadata")
    except (UnicodeError, RecursionError, TypeError, OverflowError) as exc:
        raise ValueError("invalid report JSON") from exc
    keys = {
        "schema_version",
        "parquet_sha256",
        "parquet_size_bytes",
        "input_manifest_sha256",
        "listings_sha256",
        "rows",
    }
    if type(document) is not dict or set(document) != keys:
        raise ValueError("report schema")
    if document["schema_version"] != SCHEMA:
        raise ValueError("report version")
    for field in ("parquet_sha256", "input_manifest_sha256", "listings_sha256"):
        if type(document[field]) is not str or not _SHA.fullmatch(document[field]):
            raise ValueError("report hash grammar")
    if (
        type(document["parquet_size_bytes"]) is not int
        or document["parquet_size_bytes"] != parquet_size_bytes
        or document["parquet_sha256"] != parquet_sha256
        or document["input_manifest_sha256"] != hashlib.sha256(manifest).hexdigest()
        or document["listings_sha256"] != hashlib.sha256(listings).hexdigest()
    ):
        raise ValueError("report byte commitment")
    if (
        type(input_manifest) is not dict
        or set(input_manifest)
        != {
            "schema_version",
            "hdf_sha256",
            "listings_sha256",
            "output_event_ns",
            "max_rows",
            "max_bytes",
        }
        or input_manifest["schema_version"] != "mineagent.factor-normalization.v2"
        or type(input_manifest["hdf_sha256"]) is not str
        or not _SHA.fullmatch(input_manifest["hdf_sha256"])
        or input_manifest["listings_sha256"] != document["listings_sha256"]
        or type(input_manifest["output_event_ns"]) is not int
        or not -(2**63) <= input_manifest["output_event_ns"] < 2**63
        or type(input_manifest["max_rows"]) is not int
        or not 1 <= input_manifest["max_rows"] <= MAX_ROWS
        or type(input_manifest["max_bytes"]) is not int
        or not 1 <= input_manifest["max_bytes"] <= MAX_BYTES
    ):
        raise ValueError("report input context")
    if type(listing_rows) is not list or not 1 <= len(listing_rows) <= MAX_ROWS:
        raise ValueError("report listings")
    codes, identifiers = [], []
    for row in listing_rows:
        if (
            type(row) is not dict
            or set(row) != {"code", "listing_id"}
            or type(row["code"]) is not int
            or not 0 <= row["code"] < 2**32
            or type(row["listing_id"]) is not str
            or not row["listing_id"]
        ):
            raise ValueError("report listing grammar")
        codes.append(row["code"])
        identifiers.append(row["listing_id"])
    if codes != sorted(set(codes)) or len(set(identifiers)) != len(identifiers):
        raise ValueError("report listing identity")
    rows = document["rows"]
    if (
        type(rows) is not list
        or len(rows) != len(codes)
        or len(rows) > input_manifest["max_rows"]
    ):
        raise ValueError("report row coverage")
    result = []
    for expected_code, row in zip(codes, rows, strict=True):
        if (
            type(row) is not list
            or len(row) != 4
            or type(row[0]) is not int
            or row[0] != input_manifest["output_event_ns"]
            or type(row[1]) is not int
            or row[1] != expected_code
            or type(row[3]) is not int
            or row[3] not in (0, 1)
            or (
                row[3] == 0 and (type(row[2]) is not float or not math.isfinite(row[2]))
            )
            or (row[3] == 1 and row[2] is not None)
        ):
            raise ValueError("report primitive row")
        result.append(tuple(row))
    return tuple(result)


def build_report(rows, *, parquet, manifest, listings):
    raw = canonical(
        {
            "schema_version": SCHEMA,
            "parquet_sha256": hashlib.sha256(parquet).hexdigest(),
            "parquet_size_bytes": len(parquet),
            "input_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "listings_sha256": hashlib.sha256(listings).hexdigest(),
            "rows": [list(row) for row in rows],
        }
    )
    validate_report(raw, parquet=parquet, manifest=manifest, listings=listings)
    return raw
