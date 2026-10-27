"""Owned retained synthetic marketdata, never a live/PIT certification.

Uses the real marketdata codec, DatasetVersion namespace and configured reader.
No old demo DatasetBinding or arbitrary caller label DTO is substituted.
"""

import copy
import hashlib
import json
from datetime import UTC, date, datetime
from uuid import uuid4

from backend.app.core.atomic_storage import provision_directory
from backend.app.models import DatasetVersion
from packages.marketdata.contracts import Bar, BarsRequest, PriceSemantics, make_listing
from packages.marketdata.normalize import canonical_json
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def retained_marketdata_fixture():
    listings = [
        make_listing(
            symbol=symbol,
            mic="XNYS",
            currency="USD",
            timezone="America/New_York",
            calendar="XNYS",
            vendor="fixed_fixture",
            name=symbol,
            identity_status="user_supplied",
        )
        for symbol in ("AAA", "BBB", "CCC")
    ]
    request = BarsRequest(
        listings=listings,
        start=date(2024, 3, 11),
        end=date(2024, 3, 15),
        as_of=datetime(2024, 3, 20, tzinfo=UTC),
    )
    sessions = [
        {
            "session_date": f"2024-03-{day:02d}",
            "open_time": f"2024-03-{day:02d}T13:30:00Z",
            "close_time": f"2024-03-{day:02d}T20:00:00Z",
            "next_open_time": f"2024-03-{day + 1:02d}T13:30:00Z",
        }
        for day in (11, 12, 13, 14)
    ]
    schedule = {
        "schema_version": "marketdata.session-schedule.v1",
        "generator": {"name": "exchange_calendars", "version": "4.13.2"},
        "start": "2024-03-11",
        "end": "2024-03-15",
        "listing_calendars": {
            listing.listing_id: {
                "mic": listing.mic,
                "calendar": listing.calendar,
                "calendar_basis": "exchange_calendar",
                "note": "Fixed test clock, not live retrieval",
            }
            for listing in listings
        },
        "calendars": {"XNYS": {"status": "complete", "sessions": sessions}},
    }
    bars = [
        Bar(
            listing_id=listing.listing_id,
            session_date=date(2024, 3, day),
            open=100,
            high=130,
            low=99,
            close={
                "AAA": {11: 100, 12: 102, 13: 104, 14: 110},
                "BBB": {11: 100, 12: 101, 13: 102, 14: 104},
                "CCC": {11: 100, 12: 99, 13: 98, 14: 96},
            }[listing.symbol][day],
            volume=1000,
            currency="USD",
            volume_unit="shares",
        ).model_dump(mode="json")
        for day in (11, 12, 13, 14)
        for listing in listings
    ]
    semantics = PriceSemantics(
        native_mode="raw", split_basis="unadjusted", dividend_basis="unadjusted"
    )
    manifest = {
        "schema_version": "marketdata.v1",
        "transformation_version": "normalize.v1",
        "request": request.model_dump(mode="json"),
        "listings": [x.model_dump(mode="json") for x in listings],
        "price_semantics": semantics.model_dump(mode="json"),
        "universe": {"state": "user_fixed", "point_in_time": False},
        "data_evidence": "synthetic_fixture_not_live",
        "is_synthetic": True,
        "ready": True,
    }
    return {
        "dataset_id": uuid4(),
        "manifest": manifest,
        "bars": bars,
        "schedule": schedule,
    }


def encoded_marketdata_fixture(value):
    manifest = copy.deepcopy(value["manifest"])
    artifacts = {
        "preview.json": canonical_json(value["bars"]),
        "sessions.json": canonical_json(value["schedule"]),
    }
    manifest.update(
        artifact_files={key: sha(raw) for key, raw in artifacts.items()},
        row_count=len(value["bars"]),
        bars_sha256=sha(canonical_json(value["bars"])),
    )
    manifest["content_hash"] = sha(canonical_json(manifest))
    raw = canonical_json(manifest)
    return {
        "dataset_id": value["dataset_id"],
        "manifest_sha256": sha(raw),
        "manifest_bytes": raw,
        "artifacts": artifacts,
        "policy": ConfiguredSourcePolicy(acknowledged_unverified=True),
        "input_names": ("close", "volume"),
    }


def persist_configured_dataset(factory, owner_id, settings):
    """Retain real canonical artifact bytes and exact owned row; no run/source grant."""
    args = encoded_marketdata_fixture(retained_marketdata_fixture())
    key = f"datasets/{owner_id}/{args['dataset_id']}"
    directory = settings.storage_root / key
    provision_directory(directory, durable_parent=settings.storage_root)
    for name, raw in {
        "manifest.json": args["manifest_bytes"],
        **args["artifacts"],
    }.items():
        with (directory / name).open("xb") as stream:
            stream.write(raw)
    dataset = DatasetVersion(
        id=args["dataset_id"],
        owner_id=owner_id,
        name="Retained configured fixed-clock marketdata",
        market="US",
        start_date=date(2024, 3, 11),
        end_date=date(2024, 3, 15),
        source="fixed_fixture",
        is_synthetic=True,
        content_sha256=args["manifest_sha256"],
        manifest=json.loads(args["manifest_bytes"]),
        storage_key=key,
    )
    with factory.begin() as session:
        session.add(dataset)
    source = parse_retained_source(**args)
    return dataset, source, args["policy"]
