"""Inert, bounded two-upload research basket; never an ownership grant or PIT proof.

Only original CSV bytes with retained schema-2 declaration evidence are accepted.
No filesystem access, provider, calendar inference, filling or code execution.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from packages.marketdata.contracts import Bar, BarsRequest, Coverage, UploadMapping
from packages.strategy_sdk import JsonLimits, canonical_json, parse_json
from packages.strategy_sdk.base import utc_timestamp
from packages.tradegrad_adapter.portable_windows import _declared_upload

PROFILE = "mineagent.declared-upload-basket.v1"
EVIDENCE = "user_declaration_unverified"
LIMITS = JsonLimits(max_bytes=16_000_000, max_nodes=500_000, max_items=20_000)
MAX_BYTES = 64_000_000
MAX_FILES = 128
MAX_BASKET_FILES = MAX_FILES * 2 + 4
MAX_SESSIONS = 5000


@dataclass(frozen=True)
class UploadSource:
    dataset_id: UUID
    owner_id: UUID
    manifest_sha256: str
    manifest_bytes: bytes
    artifacts: dict[str, bytes]


@dataclass(frozen=True)
class BuiltBasket:
    manifest_bytes: bytes
    files: tuple[tuple[str, bytes], ...]
    sha256: str


def _require(condition, message="Invalid declared upload basket"):
    if not condition:
        raise ValueError(message)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    return parse_json(raw, limits=LIMITS)


def _bytes(value):
    return canonical_json(value, limits=LIMITS)


def _name(value):
    return (
        type(value) is str
        and len(value) <= 240
        and all(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", p) and p not in {".", ".."}
            for p in value.split("/")
        )
    )


def _artifact_map(manifest):
    refs = {}

    def add(name, digest):
        _require(
            _name(name)
            and name != "manifest.json"
            and type(digest) is str
            and re.fullmatch(r"[0-9a-f]{64}", digest)
        )
        _require(name not in refs or refs[name] == digest)
        refs[name] = digest

    for key in ("files", "artifact_files"):
        values = manifest.get(key)
        _require(type(values) is dict and len(values) <= MAX_FILES)
        for name, digest in values.items():
            add(name, digest)
    pages = manifest.get("preview_files")
    _require(type(pages) is dict and len(pages) == 1)
    for values in pages.values():
        _require(type(values) is list and 1 <= len(values) <= 10)
        for ref in values:
            _require(
                type(ref) is dict
                and set(ref) == {"path", "sha256", "start_date", "end_date"}
            )
            add(ref["path"], ref["sha256"])
    _require(len(refs) <= MAX_FILES)
    return refs


def _sessions(schedule, request):
    _require(
        type(schedule) is dict
        and schedule.get("schema_version") == "marketdata.session-schedule.v1"
        and schedule.get("generator")
        == {"name": "exchange_calendars", "version": "4.13.2"}
        and (schedule.get("start"), schedule.get("end"))
        == (str(request.start), str(request.end))
    )
    bindings = schedule.get("listing_calendars")
    listing = request.listings[0]
    _require(type(bindings) is dict and set(bindings) == {listing.listing_id})
    binding = bindings[listing.listing_id]
    _require(
        type(binding) is dict
        and set(binding) == {"mic", "calendar", "calendar_basis", "note"}
        and binding["mic"] == "XNYS"
        and binding["calendar"] == "XNYS"
        and binding["calendar_basis"] == "exchange_calendar"
        and type(binding["note"]) is str
    )
    calendars = schedule.get("calendars")
    _require(type(calendars) is dict and set(calendars) == {"XNYS"})
    clock = calendars["XNYS"]
    _require(type(clock) is dict and clock.get("status") == "complete")
    sessions = clock.get("sessions")
    _require(type(sessions) is list and 1 <= len(sessions) <= MAX_SESSIONS)
    previous = None
    for row in sessions:
        _require(
            type(row) is dict
            and set(row)
            == {"session_date", "open_time", "close_time", "next_open_time"}
        )
        day = date.fromisoformat(row["session_date"])
        opening, closing, following = [
            utc_timestamp(row[k]) for k in ("open_time", "close_time", "next_open_time")
        ]
        _require(request.start <= day < request.end and opening < closing < following)
        if previous:
            _require(
                previous["session_date"] < row["session_date"]
                and previous["next_open_time"] == row["open_time"]
            )
        previous = row
    return [row["session_date"] for row in sessions]


def _csv_rows(raw, mapping, request, days):
    _require(type(raw) is bytes and 0 < len(raw) <= 10_000_000)
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
    header = next(reader, [])
    required = {"session_date", "open", "high", "low", "close", "volume"}
    _require(1 <= len(header) <= 50 and len(set(header)) == len(header))
    _require(
        set(mapping.columns) == required
        and len(set(mapping.columns.values())) == 6
        and set(mapping.columns.values()) <= set(header)
        and mapping.date_format == "%Y-%m-%d"
    )
    actions = [
        h
        for h in header
        if re.sub(r"[ _-]", "", h).lower()
        in {
            "dividend",
            "dividends",
            "split",
            "splits",
            "stocksplit",
            "stocksplits",
            "capitalgain",
            "capitalgains",
        }
    ]
    rows = []
    listing = request.listings[0]
    for values in reader:
        if not values:
            continue
        _require(len(values) == len(header) and len(rows) < MAX_SESSIONS)
        row = dict(zip(header, values, strict=True))
        day = datetime.strptime(row[mapping.columns["session_date"]], "%Y-%m-%d").date()
        # Reject out-of-range source rows; never silently crop an original upload.
        _require(request.start <= day < request.end)
        for key in actions:
            value = float(row[key])
            _require(math.isfinite(value) and value == 0)
        fields = {
            key: float(row[mapping.columns[key]]) for key in required - {"session_date"}
        }
        _require(all(math.isfinite(v) for v in fields.values()))
        _require(
            min(fields[k] for k in ("open", "high", "low", "close")) > 0
            and fields["volume"] >= 0
        )
        _require(
            fields["low"] <= min(fields[k] for k in ("open", "close", "high"))
            and fields["high"] >= max(fields[k] for k in ("open", "close", "low"))
        )
        _require(
            (listing.listed_on is None or listing.listed_on <= day)
            and (listing.delisted_on is None or day < listing.delisted_on)
        )
        rows.append(
            Bar(
                listing_id=listing.listing_id,
                session_date=day,
                currency="USD",
                volume_unit="shares",
                **fields,
            ).model_dump(mode="json")
        )
    rows.sort(key=lambda r: r["session_date"])
    _require(
        [r["session_date"] for r in rows] == days,
        "Uploads require the complete identical session sequence",
    )
    return rows


def _source(source, owner_id):
    _require(
        type(source) is UploadSource
        and type(source.dataset_id) is UUID
        and source.owner_id == owner_id
        and type(source.owner_id) is UUID
    )
    _require(
        type(source.manifest_bytes) is bytes
        and len(source.manifest_bytes) <= 1_000_000
        and _hash(source.manifest_bytes) == source.manifest_sha256
    )
    manifest = _json(source.manifest_bytes)
    _require(
        type(manifest) is dict
        and type(manifest.get("schema_version")) is int
        and manifest["schema_version"] == 2
        and manifest.get("market") == "US"
        and manifest.get("ready") is True
        and manifest.get("is_synthetic") is False
        and manifest.get("data_evidence") == EVIDENCE
    )
    unhashed = dict(manifest)
    digest = unhashed.pop("content_hash", None)
    _require(_hash(_bytes(unhashed)) == digest)
    refs = _artifact_map(manifest)
    _require(type(source.artifacts) is dict and set(source.artifacts) == set(refs))
    _require(
        all(
            type(raw) is bytes and len(raw) <= 16_000_000 and _hash(raw) == refs[name]
            for name, raw in source.artifacts.items()
        )
    )
    _require(sum(map(len, source.artifacts.values())) <= MAX_BYTES // 2)
    read = lambda name: _json(source.artifacts[name])
    request = BarsRequest.model_validate(manifest.get("request"))
    _require(len(request.listings) == 1 and request.repair_source is None)
    listing = request.listings[0]
    listings = [listing.model_dump(mode="json")]
    _require(
        manifest.get("listings") == listings
        and (listing.mic, listing.calendar, listing.currency, listing.timezone)
        == ("XNYS", "XNYS", "USD", "America/New_York")
    )
    _require(
        (manifest.get("universe") or {}).get("state") == "user_fixed"
        and (
            (manifest.get("universe") or {}).get("point_in_time") is None
            or (manifest.get("universe") or {}).get("point_in_time") is False
        )
    )
    members = manifest["universe"].get("members")
    _require(
        type(members) is list
        and len(members) == 1
        and type(members[0]) is dict
        and members[0].get("listing_id") == listing.listing_id
        and all(
            members[0].get(k) is None for k in ("valid_from", "valid_to", "known_at")
        )
    )
    _require(
        (manifest.get("evaluation_compatibility") or {}).get("compatible") is True
        and not (manifest.get("quality") or {}).get("unknown_flags")
    )
    _require(
        type(manifest.get("warnings")) is list
        and all(
            type(w) is dict and w.get("severity") != "error"
            for w in manifest["warnings"]
        )
    )
    actions = read("actions.json")
    _require(type(actions) is list and not actions)
    coverage = read("corporate-action-coverage.json")
    _require(
        type(coverage) is dict
        and coverage.get("schema_version") == "marketdata.corporate-action-coverage.v2"
        and (coverage.get("start"), coverage.get("end"))
        == (str(request.start), str(request.end))
    )
    _declared_upload(
        coverage,
        manifest,
        request.model_dump(mode="json"),
        listings,
        actions,
        read,
        source.artifacts.__getitem__,
    )
    original = manifest["upload_source"]
    _require(original["artifact"] == "source.csv")
    params = dict(manifest["provenance"][0]["effective_params"])
    params.pop("upload_source")
    mapping = UploadMapping.model_validate(params)
    _require(
        mapping.volume_unit == "shares"
        and mapping.price_semantics.native_mode == "raw"
        and mapping.price_semantics.adjustment_as_of is None
        and mapping.price_semantics.algorithm_version is None
        and mapping.price_semantics.actions_snapshot_id is None
    )
    _require(
        read("provenance.json") == manifest["provenance"]
        and read("coverage.json") == manifest["coverage"]
    )
    schedule = read("sessions.json")
    days = _sessions(schedule, request)
    coverages = manifest["coverage"]
    _require(type(coverages) is list and len(coverages) == 1)
    cov = Coverage.model_validate(coverages[0])
    _require(
        cov.listing_id == listing.listing_id
        and cov.status == "complete"
        and cov.calendar == "XNYS"
        and (cov.requested_start, cov.requested_end) == (request.start, request.end)
        and list(map(str, cov.expected_sessions)) == days
        and list(map(str, cov.observed_sessions)) == days
        and not any(
            (
                cov.missing_sessions,
                cov.provisional_sessions,
                cov.non_session_dates,
                cov.before_listing_sessions,
                cov.after_delisting_sessions,
                cov.known_suspension_sessions,
            )
        )
    )
    rows = _csv_rows(source.artifacts["source.csv"], mapping, request, days)
    _require(
        read("preview.json") == rows
        and manifest.get("row_count") == len(rows)
        and type(manifest.get("row_count")) is int
        and manifest.get("bars_sha256") == _hash(_bytes(rows))
    )
    _require(set(manifest["preview_files"]) == {listing.listing_id})
    page_rows = []
    for ref in manifest["preview_files"][listing.listing_id]:
        chunk = read(ref["path"])
        _require(type(chunk) is list and 1 <= len(chunk) <= 500)
        _require(
            ref["start_date"] == chunk[0]["session_date"]
            and ref["end_date"]
            == str(date.fromisoformat(chunk[-1]["session_date"]) + timedelta(days=1))
        )
        page_rows.extend(chunk)
    _require(page_rows == rows)
    return manifest, listing.model_dump(mode="json"), rows, schedule


def _build_declared_basket(
    *, owner_id: UUID, dataset_id: UUID, sources: tuple[UploadSource, UploadSource]
) -> BuiltBasket:
    _require(
        type(owner_id) is UUID
        and type(dataset_id) is UUID
        and type(sources) is tuple
        and len(sources) == 2
    )
    _require(
        all(type(s) is UploadSource and type(s.dataset_id) is UUID for s in sources)
    )
    _require(all(type(s.artifacts) is dict for s in sources))
    # Snapshot caller-owned containers; only immutable bytes survive the boundary.
    sources = tuple(
        UploadSource(
            s.dataset_id,
            s.owner_id,
            s.manifest_sha256,
            s.manifest_bytes,
            dict(s.artifacts),
        )
        for s in sources
    )
    sources = tuple(sorted(sources, key=lambda s: str(s.dataset_id)))
    _require(len({s.dataset_id for s in sources} | {dataset_id}) == 3)
    results = [_source(s, owner_id) for s in sources]
    _require(
        len({r[1]["listing_id"] for r in results}) == 2
        and len({(r[1]["mic"], r[1]["symbol"]) for r in results}) == 2
        and len({r[1]["instrument_id"] for r in results}) == 2
    )
    _require(
        {k: v for k, v in results[0][3].items() if k != "listing_calendars"}
        == {k: v for k, v in results[1][3].items() if k != "listing_calendars"},
        "Uploads must have the same full pinned schedule",
    )
    start, end = results[0][0]["request"]["start"], results[0][0]["request"]["end"]
    _require(
        all(
            (r[0]["request"]["start"], r[0]["request"]["end"]) == (start, end)
            for r in results
        )
    )
    files = {}
    source_refs = []
    for index, (source, result) in enumerate(zip(sources, results, strict=True)):
        prefix = f"sources/{index}/"
        files[prefix + "manifest.json"] = source.manifest_bytes
        files.update({prefix + name: raw for name, raw in source.artifacts.items()})
        source_refs.append(
            {
                "ordinal": index,
                "dataset_version_id": str(source.dataset_id),
                "manifest_sha256": source.manifest_sha256,
                "listing_id": result[1]["listing_id"],
                "artifact_prefix": prefix,
            }
        )
    rows = sorted(
        results[0][2] + results[1][2],
        key=lambda r: (r["session_date"], r["listing_id"]),
    )
    files["preview.json"] = _bytes(rows)
    combined_schedule = dict(results[0][3])
    combined_schedule["listing_calendars"] = (
        results[0][3]["listing_calendars"] | results[1][3]["listing_calendars"]
    )
    files["sessions.json"] = _bytes(combined_schedule)
    _require(sum(map(len, files.values())) <= MAX_BYTES)
    manifest = {
        "schema_version": 3,
        "profile": PROFILE,
        "owner_id": str(owner_id),
        "dataset_version_id": str(dataset_id),
        "start": start,
        "end": end,
        "market": "US",
        "is_synthetic": False,
        "data_evidence": EVIDENCE,
        "point_in_time_proven": False,
        "sources": source_refs,
        "listings": [r[1] for r in results],
        "row_count": len(rows),
        "artifact_files": {name: _hash(raw) for name, raw in sorted(files.items())},
    }
    raw = _bytes(manifest)
    return BuiltBasket(raw, tuple(sorted(files.items())), _hash(raw))


def _validate_declared_basket(
    manifest_bytes: bytes,
    artifacts: dict[str, bytes],
    *,
    dataset_id: UUID,
    owner_id: UUID,
) -> BuiltBasket:
    _require(
        type(manifest_bytes) is bytes
        and len(manifest_bytes) <= 1_000_000
        and type(artifacts) is dict
        and len(artifacts) <= MAX_BASKET_FILES
    )
    _require(
        all(
            type(name) is str
            and _name(name)
            and type(raw) is bytes
            and len(raw) <= 16_000_000
            for name, raw in artifacts.items()
        )
    )
    _require(sum(map(len, artifacts.values())) <= MAX_BYTES)
    manifest = _json(manifest_bytes)
    _require(
        type(manifest) is dict
        and manifest.get("schema_version") == 3
        and type(manifest.get("schema_version")) is int
        and manifest.get("profile") == PROFILE
    )
    refs = manifest.get("sources")
    _require(
        type(refs) is list
        and len(refs) == 2
        and type(artifacts) is dict
        and len(artifacts) <= MAX_BASKET_FILES
    )
    sources = []
    for index, ref in enumerate(refs):
        prefix = f"sources/{index}/"
        _require(
            type(ref) is dict
            and ref.get("artifact_prefix") == prefix
            and ref.get("ordinal") == index
            and type(ref.get("ordinal")) is int
        )
        raw = artifacts[prefix + "manifest.json"]
        source_manifest = _json(raw)
        names = _artifact_map(source_manifest)
        sources.append(
            UploadSource(
                UUID(ref["dataset_version_id"]),
                owner_id,
                ref["manifest_sha256"],
                raw,
                {name: artifacts[prefix + name] for name in names},
            )
        )
    result = build_declared_basket(
        owner_id=owner_id, dataset_id=dataset_id, sources=tuple(sources)
    )
    _require(
        manifest_bytes == result.manifest_bytes and artifacts == dict(result.files),
        "Basket differs from its exact original uploads",
    )
    return result


def build_declared_basket(
    *, owner_id: UUID, dataset_id: UUID, sources: tuple[UploadSource, UploadSource]
) -> BuiltBasket:
    """Validate inert evidence only; host separately owns authorization/publication."""
    try:
        return _build_declared_basket(
            owner_id=owner_id, dataset_id=dataset_id, sources=sources
        )
    except (
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        UnicodeError,
        OverflowError,
        csv.Error,
    ) as exc:
        raise ValueError("Malformed declared upload evidence") from exc


def validate_declared_basket(
    manifest_bytes: bytes,
    artifacts: dict[str, bytes],
    *,
    dataset_id: UUID,
    owner_id: UUID,
) -> BuiltBasket:
    """Rebuild all original-bound bytes, never trust a prior parser result."""
    try:
        return _validate_declared_basket(
            manifest_bytes, artifacts, dataset_id=dataset_id, owner_id=owner_id
        )
    except (
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        UnicodeError,
        OverflowError,
        csv.Error,
    ) as exc:
        raise ValueError("Malformed declared upload evidence") from exc
