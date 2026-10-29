"""Artificial inert source bytes, not live market or authorization evidence."""

import hashlib
import json
from dataclasses import replace
from uuid import UUID

import pytest

from packages.marketdata.contracts import (
    Bar,
    BarsRequest,
    Coverage,
    Listing,
    UploadMapping,
)
from packages.rdagent_adapter.declared_upload import (
    UploadSource,
    build_declared_basket,
    validate_declared_basket,
)
from packages.strategy_sdk import canonical_json

OWNER = UUID(int=1)
TARGET = UUID(int=2)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def source(index=0):
    listing = Listing(
        instrument_id=str(UUID(int=30 + index)),
        listing_id=str(UUID(int=40 + index)),
        symbol=f"TEST{index}",
        mic="XNYS",
        currency="USD",
        timezone="America/New_York",
        calendar="XNYS",
        identity_status="user_supplied",
    )
    request = BarsRequest(
        listings=[listing],
        start="2024-01-02",
        end="2024-01-04",
        as_of="2025-01-01T00:00:00Z",
    )
    mapping = UploadMapping(
        symbol=listing.symbol,
        mic="XNYS",
        currency="USD",
        timezone=listing.timezone,
        calendar="XNYS",
        volume_unit="shares",
        columns={
            k: k for k in ("session_date", "open", "high", "low", "close", "volume")
        },
        price_semantics={
            "native_mode": "raw",
            "split_basis": "unadjusted",
            "dividend_basis": "unadjusted",
        },
        portable_declaration={
            "kind": "user_declared_raw_no_actions",
            "acknowledged_unverified": True,
            "symbol": listing.symbol,
            "mic": "XNYS",
            "start": "2024-01-02",
            "end": "2024-01-04",
            "source_reference": "Artificial original uploaded CSV, not market evidence",
            "evidence_note": "Test only explicit user declaration, not independently verified.",
        },
    )
    raw = b"session_date,open,high,low,close,volume\n2024-01-02,10,12,9,11,100\n2024-01-03,11,13,10,12,120\n"
    original = {"artifact": "source.csv", "sha256": digest(raw), "size_bytes": len(raw)}
    provenance = {
        "provenance_id": f"source-{index}",
        "connector": "upload",
        "provider": "user",
        "endpoint": "uploaded_table",
        "evidence": "uploaded",
        "requested_params": {
            "listing_id": listing.listing_id,
            "start": "2024-01-02",
            "end": "2024-01-04",
        },
        "effective_params": mapping.model_dump(mode="json")
        | {"upload_source": original},
    }
    evidence = {
        "schema_version": "marketdata.portable-upload-evidence.v1",
        "data_evidence": "user_declaration_unverified",
        "listing_id": listing.listing_id,
        "start": "2024-01-02",
        "end": "2024-01-04",
        "declaration": mapping.portable_declaration.model_dump(mode="json"),
        "source": original,
        "provenance_id": provenance["provenance_id"],
        "price_semantics": mapping.price_semantics.model_dump(mode="json"),
    }
    coverage = {
        "schema_version": "marketdata.corporate-action-coverage.v2",
        "start": "2024-01-02",
        "end": "2024-01-04",
        "listings": [
            {
                "listing_id": listing.listing_id,
                "status": "declared_none",
                "provenance_ids": [provenance["provenance_id"]],
                "evidence": "user_declaration_unverified",
            }
        ],
        "is_synthetic": False,
        "generator_version": None,
        "declaration": {
            "artifact": "portable-upload-declaration.json",
            "sha256": digest(canonical_json(evidence)),
        },
    }
    rows = [
        Bar(
            listing_id=listing.listing_id,
            session_date=day,
            open=o,
            high=o + 2,
            low=o - 1,
            close=o + 1,
            volume=v,
            currency="USD",
            volume_unit="shares",
        ).model_dump(mode="json")
        for day, o, v in [("2024-01-02", 10, 100), ("2024-01-03", 11, 120)]
    ]
    days = [r["session_date"] for r in rows]
    cov = Coverage(
        listing_id=listing.listing_id,
        requested_start=request.start,
        requested_end=request.end,
        actual_start=request.start,
        actual_end=request.end,
        status="complete",
        calendar="XNYS",
        calendar_version="4.13.2",
        expected_sessions=days,
        observed_sessions=days,
    ).model_dump(mode="json")
    schedule = {
        "schema_version": "marketdata.session-schedule.v1",
        "generator": {"name": "exchange_calendars", "version": "4.13.2"},
        "start": "2024-01-02",
        "end": "2024-01-04",
        "listing_calendars": {
            listing.listing_id: {
                "mic": "XNYS",
                "calendar": "XNYS",
                "calendar_basis": "exchange_calendar",
                "note": "XNYS exchange-calendars regular-equity schedule.",
            }
        },
        "calendars": {
            "XNYS": {
                "status": "complete",
                "sessions": [
                    {
                        "session_date": day,
                        "open_time": day + "T14:30:00Z",
                        "close_time": day + "T21:00:00Z",
                        "next_open_time": nxt + "T14:30:00Z",
                    }
                    for day, nxt in [
                        ("2024-01-02", "2024-01-03"),
                        ("2024-01-03", "2024-01-04"),
                    ]
                ],
            }
        },
    }
    values = {
        "portable-upload-declaration.json": evidence,
        "corporate-action-coverage.json": coverage,
        "raw.json": [
            {
                "listing_id": listing.listing_id,
                "provenance_id": provenance["provenance_id"],
                "content": original,
            }
        ],
        "provenance.json": [provenance],
        "coverage.json": [cov],
        "actions.json": [],
        "quality.json": [],
        "sessions.json": schedule,
        "preview.json": rows,
    }
    blobs = {name: canonical_json(value) for name, value in values.items()}
    blobs["source.csv"] = raw
    manifest = {
        "schema_version": 2,
        "market": "US",
        "ready": True,
        "is_synthetic": False,
        "data_evidence": "user_declaration_unverified",
        "request": request.model_dump(mode="json"),
        "listings": [listing.model_dump(mode="json")],
        "price_semantics": mapping.price_semantics.model_dump(mode="json"),
        "universe": {
            "state": "user_fixed",
            "members": [
                {
                    "listing_id": listing.listing_id,
                    "valid_from": None,
                    "valid_to": None,
                    "known_at": None,
                }
            ],
            "evidence": [],
        },
        "evaluation_compatibility": {"compatible": True},
        "quality": {"unknown_flags": []},
        "warnings": [],
        "upload_source": original,
        "provenance": [provenance],
        "coverage": [cov],
        "files": {},
        "artifact_files": {name: digest(raw) for name, raw in blobs.items()},
        "row_count": 2,
        "bars_sha256": digest(canonical_json(rows)),
        "preview_files": {
            listing.listing_id: [
                {
                    "path": f"preview/{listing.listing_id}/0.json",
                    "sha256": digest(canonical_json(rows)),
                    "start_date": "2024-01-02",
                    "end_date": "2024-01-04",
                }
            ]
        },
    }
    blobs[f"preview/{listing.listing_id}/0.json"] = canonical_json(rows)
    manifest["content_hash"] = digest(canonical_json(manifest))
    encoded = canonical_json(manifest)
    return UploadSource(UUID(int=10 + index), OWNER, digest(encoded), encoded, blobs)


def repin(item, mutate_manifest=None, mutate_blobs=None):
    manifest = json.loads(item.manifest_bytes)
    blobs = dict(item.artifacts)
    if mutate_blobs:
        mutate_blobs(blobs)
    for name in manifest["artifact_files"]:
        manifest["artifact_files"][name] = digest(blobs[name])
    for pages in manifest["preview_files"].values():
        for ref in pages:
            ref["sha256"] = digest(blobs[ref["path"]])
    if mutate_manifest:
        mutate_manifest(manifest)
    manifest.pop("content_hash")
    manifest["content_hash"] = digest(canonical_json(manifest))
    encoded = canonical_json(manifest)
    return replace(
        item, manifest_bytes=encoded, manifest_sha256=digest(encoded), artifacts=blobs
    )


def build(a=None, b=None):
    return build_declared_basket(
        owner_id=OWNER, dataset_id=TARGET, sources=(a or source(), b or source(1))
    )


def test_exact_originals_preserved_and_revalidated():
    a, b = source(), source(1)
    result = build(a, b)
    manifest = json.loads(result.manifest_bytes)
    assert (
        manifest["is_synthetic"] is False and manifest["point_in_time_proven"] is False
    )
    assert manifest["data_evidence"] == "user_declaration_unverified"
    assert dict(result.files)["sources/0/source.csv"] == a.artifacts["source.csv"]
    assert dict(result.files)["sources/0/manifest.json"] == a.manifest_bytes
    assert result == build_declared_basket(
        owner_id=OWNER, dataset_id=TARGET, sources=(b, a)
    )
    assert (
        validate_declared_basket(
            result.manifest_bytes, dict(result.files), dataset_id=TARGET, owner_id=OWNER
        )
        == result
    )


@pytest.mark.parametrize(
    "change",
    [
        {"owner_id": UUID(int=99)},
        {"manifest_sha256": "0" * 64},
        {"dataset_id": TARGET},
        {"manifest_bytes": b"{}"},
    ],
)
def test_source_identity_rejected(change):
    with pytest.raises(ValueError):
        build(replace(source(), **change))


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", True),
        ("schema_version", 1),
        ("market", "CN"),
        ("ready", False),
        ("is_synthetic", True),
        ("data_evidence", "verified"),
        ("evaluation_compatibility", {"compatible": False}),
        ("quality", {"unknown_flags": ["volume"]}),
        ("warnings", [{"severity": "error"}]),
        ("row_count", True),
        ("universe", {"state": "user_fixed", "point_in_time": True}),
    ],
)
def test_source_semantics_fail_closed(key, value):
    with pytest.raises(ValueError):
        build(repin(source(), lambda m: m.update({key: value})))


def test_duplicate_listing_and_same_source_rejected():
    with pytest.raises(ValueError):
        build(source(), replace(source(), dataset_id=UUID(int=12)))
    with pytest.raises(ValueError):
        build(source(), source())


@pytest.mark.parametrize(
    "name", ["../bad", "/absolute", "x/../../bad", "x\\bad", "x//bad", "manifest.json"]
)
def test_unsafe_artifact_names_rejected(name):
    item = source()
    item = repin(
        item,
        lambda m: m["artifact_files"].update({name: digest(b"bad")}),
        lambda b: b.update({name: b"bad"}),
    )
    with pytest.raises(ValueError):
        build(item)


def test_hash_drift_and_unreferenced_artifact_rejected():
    for artifacts in (
        source().artifacts | {"source.csv": b"changed"},
        source().artifacts | {"unreferenced": b"payload"},
    ):
        with pytest.raises(ValueError):
            build(replace(source(), artifacts=artifacts))


@pytest.mark.parametrize(
    "replacement", [b"=COMMAND()", b"NaN", b"inf", b"1e999", b"-1", b"0"]
)
def test_csv_not_executed_and_invalid_prices_rejected(replacement):
    # Rebind outer hash and original byte evidence, so parser, not a stale hash, rejects.
    item = source()
    raw = item.artifacts["source.csv"].replace(b",10,12,", b"," + replacement + b",12,")
    with pytest.raises(ValueError):
        build(rebind_csv(item, raw))


def rebind_csv(item, raw):
    def blobs(b):
        original = {
            "artifact": "source.csv",
            "sha256": digest(raw),
            "size_bytes": len(raw),
        }
        b["source.csv"] = raw
        for name in ("portable-upload-declaration.json", "raw.json", "provenance.json"):
            value = json.loads(b[name])
            if name.startswith("portable"):
                value["source"] = original
            elif name == "raw.json":
                value[0]["content"] = original
            else:
                value[0]["effective_params"]["upload_source"] = original
            b[name] = canonical_json(value)
        cov = json.loads(b["corporate-action-coverage.json"])
        cov["declaration"]["sha256"] = digest(b["portable-upload-declaration.json"])
        b["corporate-action-coverage.json"] = canonical_json(cov)

    modified = dict(item.artifacts)
    blobs(modified)
    return repin(
        replace(item, artifacts=modified),
        lambda m: m.update(
            upload_source=json.loads(modified["portable-upload-declaration.json"])[
                "source"
            ],
            provenance=json.loads(modified["provenance.json"]),
        ),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"session_date,open,high,low,close,volume\n2024-01-02,10,12,9,11,100\n",
        b"session_date,open,high,low,close,volume\n2024-01-02,10,12,9,11,100\n2024-01-02,10,12,9,11,100\n",
        b"session_date,open,high,low,close,volume\n2024-01-01,10,12,9,11,100\n2024-01-03,11,13,10,12,120\n",
        b"session_date,open,high,low,close,volume,dividends\n2024-01-02,10,12,9,11,100,1\n2024-01-03,11,13,10,12,120,0\n",
        b"session_date,open,high,low,close,volume\n2024-01-02,10,12,9,11,100,extra\n",
    ],
)
def test_no_crop_fill_duplicate_or_hidden_actions(raw):
    with pytest.raises(ValueError):
        build(rebind_csv(source(), raw))


def test_rehashed_preview_cannot_replace_original():
    def mutate(blobs):
        rows = json.loads(blobs["preview.json"])
        rows[0]["close"] = 11.5
        blobs["preview.json"] = canonical_json(rows)

    with pytest.raises(ValueError):
        build(repin(source(), mutate_blobs=mutate))


def test_rehashed_different_sessions_rejected():
    def mutate(blobs):
        clock = json.loads(blobs["sessions.json"])
        clock["calendars"]["XNYS"]["sessions"].pop()
        blobs["sessions.json"] = canonical_json(clock)

    with pytest.raises(ValueError):
        build(repin(source(), mutate_blobs=mutate))


@pytest.mark.parametrize("mode", ["extra", "raw", "manifest", "owner", "dataset"])
def test_cold_validation_exact_projection(mode):
    result = build()
    blobs = dict(result.files)
    raw = result.manifest_bytes
    owner = OWNER
    target = TARGET
    if mode == "extra":
        blobs["extra"] = b"x"
    if mode == "raw":
        blobs["sources/0/source.csv"] = b"x"
    if mode == "manifest":
        value = json.loads(raw)
        value["point_in_time_proven"] = True
        raw = canonical_json(value)
    if mode == "owner":
        owner = UUID(int=99)
    if mode == "dataset":
        target = UUID(int=99)
    with pytest.raises(ValueError):
        validate_declared_basket(raw, blobs, dataset_id=target, owner_id=owner)


@pytest.mark.parametrize(
    "field",
    ["actions.json", "raw.json", "source.csv", "sessions.json", "provenance.json"],
)
def test_missing_required_evidence_raises_closed_error(field):
    item = source()
    artifacts = dict(item.artifacts)
    del artifacts[field]
    with pytest.raises(ValueError):
        build(replace(item, artifacts=artifacts))


@pytest.mark.parametrize("value", [None, [], {}, True])
def test_malformed_source_shapes_rejected(value):
    with pytest.raises(ValueError):
        build(repin(source(), lambda m: m.update(provenance=value)))


def test_source_and_artifact_byte_bounds_reject_before_csv():
    item = source()
    with pytest.raises(ValueError):
        build(replace(item, manifest_bytes=b" " * 1_000_001))
    with pytest.raises(ValueError):
        build(rebind_csv(item, b"x" * 10_000_001))
    built = build()
    with pytest.raises(ValueError):
        validate_declared_basket(
            built.manifest_bytes,
            dict(built.files) | {"extra": b"x" * 16_000_001},
            dataset_id=TARGET,
            owner_id=OWNER,
        )


def test_csv_row_limit_and_unmatched_preview_pages():
    raw = (
        b"session_date,open,high,low,close,volume\n"
        + b"2024-01-02,10,12,9,11,100\n" * 5001
    )
    with pytest.raises(ValueError):
        build(rebind_csv(source(), raw))
    item = source()
    page = next(n for n in item.artifacts if n.startswith("preview/"))
    with pytest.raises(ValueError):
        build(repin(item, mutate_blobs=lambda b: b.update({page: b"[]"})))


def test_parser_does_not_import_heavy_runtime_or_touch_network():
    import subprocess
    import sys

    script = """
import sys, socket
from packages.rdagent_adapter.declared_upload import build_declared_basket
assert not any(name in sys.modules for name in ('pandas','pyarrow','exchange_calendars','numpy'))
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)


def test_actions_must_be_explicit_empty_list_not_falsy_unknown_shape():
    with pytest.raises(ValueError):
        build(repin(source(), mutate_blobs=lambda b: b.update({"actions.json": b"{}"})))


def test_build_and_cold_validation_have_no_filesystem_or_network(monkeypatch):
    import builtins
    import socket

    a, b = source(), source(1)

    def forbidden(*args, **kwargs):
        raise AssertionError("Inert parser attempted external IO")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = build(a, b)
    assert (
        validate_declared_basket(
            result.manifest_bytes, dict(result.files), dataset_id=TARGET, owner_id=OWNER
        )
        == result
    )
