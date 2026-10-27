"""Retained canonical marketdata bytes, fixed-clock fixture only; never live/PIT."""

import copy
import hashlib
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from packages.marketdata.contracts import Bar, BarsRequest, PriceSemantics, make_listing
from packages.marketdata.normalize import canonical_json
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def retained():
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
        for symbol in ("AAA", "BBB")
    ]
    request = BarsRequest(
        listings=listings,
        start=date(2024, 3, 11),
        end=date(2024, 3, 14),
        as_of=datetime(2024, 3, 20, tzinfo=UTC),
    )
    sessions = [
        {
            "session_date": f"2024-03-{day:02d}",
            "open_time": f"2024-03-{day:02d}T13:30:00Z",
            "close_time": f"2024-03-{day:02d}T20:00:00Z",
            "next_open_time": f"2024-03-{day + 1:02d}T13:30:00Z",
        }
        for day in (11, 12, 13)
    ]
    schedule = {
        "schema_version": "marketdata.session-schedule.v1",
        "generator": {"name": "exchange_calendars", "version": "4.13.2"},
        "start": "2024-03-11",
        "end": "2024-03-14",
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
            close=101 + day,
            volume=1000,
            currency="USD",
            volume_unit="shares",
        ).model_dump(mode="json")
        for day in (11, 12, 13)
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


def encoded(value):
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


def parse(value):
    return parse_retained_source(**encoded(value))


def test_actual_marketdata_codecs_preserve_declared_not_pit(retained):
    args = encoded(retained)
    value = parse_retained_source(**args)
    assert len(value.observations) == 6
    assert value.dataset.content_sha256 == args["manifest_sha256"]
    assert value.dataset.source_quality == "declared_source"
    assert value.universe.membership_history == "declared"
    assert (
        value.qualification
        == "declared_availability_and_fixed_membership_not_verified_pit"
    )
    assert [field.name for field in value.input_fields] == ["close", "volume"]
    assert value.input_fields[1].price_semantics == "not_price"
    assert all(row.available_at == row.event_time for row in value.observations)
    assert value.source_start == date(2024, 3, 11) and value.source_end == date(
        2024, 3, 14
    )
    assert parse(retained) == value


def test_marketdata_unicode_hash_uses_original_retained_codec(retained):
    retained["manifest"]["request"]["listings"][0]["name"] = "固定股票"
    retained["manifest"]["listings"][0]["name"] = "固定股票"
    assert len(parse(retained).observations) == 6


@pytest.mark.parametrize("artifact", ["manifest", "preview.json", "sessions.json"])
def test_outer_retained_hash_drift(retained, artifact):
    args = encoded(retained)
    if artifact == "manifest":
        args["manifest_bytes"] += b" "
    else:
        args["artifacts"][artifact] += b" "
    with pytest.raises(ValueError):
        parse_retained_source(**args)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_bar",
        "missing_bar",
        "session_gap",
        "duplicate_session",
        "outside_session",
        "bad_next_open",
        "bool_price",
        "adjusted",
        "pit_claim",
        "calendar_version",
        "missing_listing_calendar",
        "extra_artifact",
        "bar_flags",
        "currency",
        "volume_unit",
    ],
)
def test_rehashed_semantic_negatives(retained, change):
    bars, manifest, schedule = (
        retained["bars"],
        retained["manifest"],
        retained["schedule"],
    )
    sessions = schedule["calendars"]["XNYS"]["sessions"]
    if change == "duplicate_bar":
        bars.append(copy.deepcopy(bars[0]))
    elif change == "missing_bar":
        bars.pop()
    elif change == "session_gap":
        sessions.pop(1)
    elif change == "duplicate_session":
        sessions.insert(1, copy.deepcopy(sessions[0]))
    elif change == "outside_session":
        bars[0]["session_date"] = "2024-03-10"
    elif change == "bad_next_open":
        sessions[0]["next_open_time"] = sessions[0]["close_time"]
    elif change == "bool_price":
        bars[0]["close"] = True
    elif change == "adjusted":
        manifest["price_semantics"]["split_basis"] = "adjusted"
    elif change == "pit_claim":
        manifest["universe"]["point_in_time"] = True
    elif change == "calendar_version":
        schedule["generator"]["version"] = "unknown"
    elif change == "missing_listing_calendar":
        schedule["listing_calendars"].pop(next(iter(schedule["listing_calendars"])))
    elif change == "bar_flags":
        bars[0]["flags"] = ["missing"]
    elif change == "currency":
        bars[0]["currency"] = "EUR"
    elif change == "volume_unit":
        bars[0]["volume_unit"] = "lots"
    args = encoded(retained)
    if change == "extra_artifact":
        args["artifacts"]["labels.json"] = b"{}"
    with pytest.raises(ValueError):
        parse_retained_source(**args)


@pytest.mark.parametrize("field,value", [("timezone", "UTC"), ("calendar", "XNAS")])
def test_mixed_listing_clock_not_merged(retained, field, value):
    manifest = retained["manifest"]
    listing = manifest["request"]["listings"][1]
    listing[field] = value
    manifest["listings"][1][field] = value
    if field == "calendar":
        retained["schedule"]["listing_calendars"][listing["listing_id"]]["calendar"] = (
            value
        )
    with pytest.raises(ValueError):
        parse(retained)


@pytest.mark.parametrize("ack", [False, 1, 0, "true", None])
def test_unverified_assumption_ack_is_literal_boolean(ack):
    with pytest.raises(ValueError):
        ConfiguredSourcePolicy(acknowledged_unverified=ack)


@pytest.mark.parametrize(
    "names",
    [("label",), ("return",), ("adj_close",), ("close", "close"), ["close"], ()],
)
def test_no_labels_or_implicit_input_projection(retained, names):
    args = encoded(retained)
    args["input_names"] = names
    with pytest.raises(ValueError):
        parse_retained_source(**args)


def test_duplicate_json_key_rejected_even_with_matching_outer_hash(retained):
    args = encoded(retained)
    raw = args["artifacts"]["preview.json"].replace(
        b'"close":112.0', b'"close":112.0,"close":112.0', 1
    )
    assert raw != args["artifacts"]["preview.json"]
    import json

    manifest = json.loads(args["manifest_bytes"])
    manifest["artifact_files"]["preview.json"] = sha(raw)
    del manifest["content_hash"]
    manifest["content_hash"] = sha(canonical_json(manifest))
    args["manifest_bytes"] = canonical_json(manifest)
    args["manifest_sha256"] = sha(args["manifest_bytes"])
    args["artifacts"]["preview.json"] = raw
    with pytest.raises(ValueError):
        parse_retained_source(**args)
