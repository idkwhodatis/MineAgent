"""Declared daily source projection over retained marketdata bytes, not PIT proof.

No file access or execution authority. The host reader must bind owner, dataset
manifest and every artifact hash. Availability/membership below are explicitly
predeclared research assumptions; provider retrieval never proves historical PIT.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import field_validator

from packages.factors.contracts import (
    CalculationUniverse,
    Contract,
    DatasetBinding,
    InputField,
    digest,
)
from packages.factors.fixtures import CalendarSession, MarketCalendar
from packages.marketdata.contracts import Bar, BarsRequest, PriceSemantics
from packages.marketdata.normalize import canonical_json as marketdata_bytes
from packages.marketdata.venues import canonical_calendar
from packages.rdagent_adapter.prefix import MarketObservation
from packages.strategy_sdk import JsonLimits, parse_json

LIMITS = JsonLimits(max_bytes=32 * 1024**2, max_nodes=2_000_000, max_items=100_000)
CALENDAR_VERSION = "4.13.2"
ARTIFACTS = ("preview.json", "sessions.json")


class ConfiguredSourcePolicy(Contract):
    schema_version: Literal["mineagent.rdagent-declared-daily-source.v1"] = (
        "mineagent.rdagent-declared-daily-source.v1"
    )
    availability: Literal["declared_session_close_not_verified"] = (
        "declared_session_close_not_verified"
    )
    membership: Literal["user_fixed_declared_at_source_start"] = (
        "user_fixed_declared_at_source_start"
    )
    price_basis: Literal["unadjusted"] = "unadjusted"
    acknowledged_unverified: Literal[True]

    @field_validator("acknowledged_unverified", mode="before")
    @classmethod
    def exact_acknowledgement(cls, value):
        if value is not True:
            raise ValueError(
                "Explicit unverified source assumption acknowledgement required"
            )
        return value


@dataclass(frozen=True)
class ConfiguredSource:
    dataset: DatasetBinding
    universe: CalculationUniverse
    calendar: MarketCalendar
    input_fields: tuple[InputField, ...]
    observations: tuple[MarketObservation, ...]
    policy_sha256: str
    artifact_sha256: tuple[tuple[str, str], ...]
    source_start: date
    source_end: date
    qualification: str = "declared_availability_and_fixed_membership_not_verified_pit"


def parse_retained_source(
    *,
    dataset_id,
    manifest_sha256,
    manifest_bytes,
    artifacts,
    policy,
    input_names,
    declared_upload=False,
    owner_id=None,
):
    """All byte identities checked again here; no arbitrary file names/labels."""
    import hashlib

    if type(dataset_id) is not UUID or type(manifest_bytes) is not bytes:
        raise ValueError("Exact retained dataset identity required")
    policy = ConfiguredSourcePolicy.model_validate(policy)
    if (
        type(input_names) is not tuple
        or not 1 <= len(input_names) <= 5
        or len(set(input_names)) != len(input_names)
        or set(input_names) - {"open", "high", "low", "close", "volume"}
    ):
        raise ValueError("Exact distinct OHLCV input fields required")
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise ValueError("Dataset manifest bytes changed")
    if type(declared_upload) is not bool:
        raise ValueError("Explicit declared-upload dispatch required")
    if declared_upload:
        from packages.rdagent_adapter.declared_projection import project_basket

        parsed_manifest = parse_json(manifest_bytes, limits=LIMITS)
        if type(parsed_manifest) is dict and parsed_manifest.get("schema_version") == 3:
            manifest = project_basket(
                manifest_bytes,
                artifacts,
                dataset_id=dataset_id,
                owner_id=owner_id,
                manifest_sha256=manifest_sha256,
            )
            return _project_source(
                manifest,
                {name: artifacts[name] for name in ARTIFACTS},
                dataset_id=dataset_id,
                manifest_sha256=manifest_sha256,
                policy=policy,
                input_names=input_names,
            )
    manifest = parse_json(manifest_bytes, limits=LIMITS)
    if (
        type(manifest) is not dict
        or manifest.get("schema_version") != "marketdata.v1"
        or manifest.get("transformation_version") != "normalize.v1"
    ):
        raise ValueError("Retained normalized marketdata.v1 required")
    # marketdata's inner content_hash excludes itself; DatasetVersion's outer hash
    # binds the complete retained manifest bytes including this inner commitment.
    inner = dict(manifest)
    content_hash = inner.pop("content_hash", None)
    if hashlib.sha256(marketdata_bytes(inner)).hexdigest() != content_hash:
        raise ValueError("Dataset inner content identity changed")
    return _project_source(
        manifest,
        artifacts,
        dataset_id=dataset_id,
        manifest_sha256=manifest_sha256,
        policy=policy,
        input_names=input_names,
    )


def _project_source(
    manifest, artifacts, *, dataset_id, manifest_sha256, policy, input_names
):
    """Common numerical projection after the separate exact source admission."""
    import hashlib

    request = BarsRequest.model_validate(manifest["request"])
    if request.interval != "1d" or manifest.get("listings") != [
        x.model_dump(mode="json") for x in request.listings
    ]:
        raise ValueError("Exact daily source/listing request required")
    semantics = PriceSemantics.model_validate(manifest["price_semantics"])
    if (
        semantics.split_basis != "unadjusted"
        or semantics.dividend_basis != "unadjusted"
    ):
        raise ValueError("Initial configured source requires explicit unadjusted OHLC")
    if (manifest.get("universe") or {}).get("state") != "user_fixed" or (
        manifest.get("universe") or {}
    ).get("point_in_time") is not False:
        raise ValueError("Only explicitly non-PIT fixed baskets are supported")
    hashes = manifest.get("artifact_files")
    if (
        type(artifacts) is not dict
        or set(artifacts) != set(ARTIFACTS)
        or type(hashes) is not dict
    ):
        raise ValueError("Exact primitive source inventory required")
    parsed = {}
    for name in ARTIFACTS:
        raw = artifacts[name]
        if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != hashes.get(
            name
        ):
            raise ValueError("Retained source artifact changed")
        parsed[name] = parse_json(raw, limits=LIMITS)
    schedule = parsed["sessions.json"]
    if (
        type(schedule) is not dict
        or schedule.get("schema_version") != "marketdata.session-schedule.v1"
        or schedule.get("generator")
        != {"name": "exchange_calendars", "version": CALENDAR_VERSION}
        or (schedule.get("start"), schedule.get("end"))
        != (str(request.start), str(request.end))
    ):
        raise ValueError("Exact retained exchange calendar generator/range required")
    listings = tuple(sorted(request.listings, key=lambda x: x.listing_id))
    if (
        not listings
        or len(listings) > 10000
        or len({x.timezone for x in listings}) != 1
        or len({x.currency for x in listings}) != 1
    ):
        raise ValueError("One timezone/currency fixed listing basket required")
    calendar_names = set()
    bindings = schedule.get("listing_calendars")
    if type(bindings) is not dict or set(bindings) != {x.listing_id for x in listings}:
        raise ValueError("Complete listing calendar identity required")
    for listing in listings:
        binding = bindings[listing.listing_id]
        if (
            type(binding) is not dict
            or binding.get("mic") != listing.mic
            or binding.get("calendar")
            != canonical_calendar(
                listing.mic,
                listing.calendar,
                currency=listing.currency,
                timezone=listing.timezone,
            )
        ):
            raise ValueError("Listing calendar binding differs")
        if binding.get("calendar_basis") not in {
            "exchange_calendar",
            "declared_proxy",
        } or not binding.get("note"):
            raise ValueError("Calendar provenance/proxy caveat required")
        calendar_names.add(binding["calendar"])
    if len(calendar_names) != 1:
        raise ValueError("Initial configured prefix requires one exact calendar")
    calendar_name = next(iter(calendar_names))
    calendars = schedule.get("calendars")
    if type(calendars) is not dict or set(calendars) != {calendar_name}:
        raise ValueError("Exact source calendar inventory required")
    selected = calendars[calendar_name]
    if (
        selected.get("status") != "complete"
        or type(selected.get("sessions")) is not list
        or not 1 <= len(selected["sessions"]) <= 4096
    ):
        raise ValueError("Complete bounded actual session clocks required")
    sessions = []
    previous_next = None
    for value in selected["sessions"]:
        if type(value) is not dict or set(value) != {
            "session_date",
            "open_time",
            "close_time",
            "next_open_time",
        }:
            raise ValueError("Exact source session clock grammar required")
        session = CalendarSession.model_validate(
            {k: value[k] for k in ("session_date", "open_time", "close_time")}
        )
        next_open = datetime.fromisoformat(value["next_open_time"])
        if (
            next_open.tzinfo is None
            or next_open <= session.close_time
            or (previous_next is not None and previous_next != session.open_time)
        ):
            raise ValueError("Session sequence/next-open clock differs")
        if not request.start <= session.session_date < request.end:
            raise ValueError("Session outside declared source interval")
        sessions.append(session)
        previous_next = next_open
    calendar = MarketCalendar(timezone=listings[0].timezone, sessions=tuple(sessions))
    days = {day.session_date: day for day in calendar.sessions}
    ids = {listing.listing_id for listing in listings}
    bars = parsed["preview.json"]
    if (
        type(bars) is not list
        or not 1 <= len(bars) <= 100000
        or manifest.get("row_count") != len(bars)
        or hashlib.sha256(marketdata_bytes(bars)).hexdigest()
        != manifest.get("bars_sha256")
    ):
        raise ValueError("Exact bounded normalized bar inventory required")
    rows, seen = [], set()
    for value in bars:
        bar = Bar.model_validate(value)
        key = (bar.session_date, bar.listing_id)
        if key in seen or bar.listing_id not in ids or bar.session_date not in days:
            raise ValueError("Duplicate or out-of-scope retained market observation")
        seen.add(key)
        if (
            bar.flags
            or bar.currency != listings[0].currency
            or bar.volume_unit != "shares"
        ):
            raise ValueError("Unsupported bar flags/currency/volume semantics")
        if any(
            isinstance(value[name], bool) or type(value[name]) not in (int, float)
            for name in ("open", "high", "low", "close", "volume")
        ):
            raise ValueError("Exact finite numeric source observations required")
        day = days[bar.session_date]
        rows.append(
            MarketObservation(
                listing_id=bar.listing_id,
                session=bar.session_date,
                event_time=day.close_time,
                available_at=day.close_time,
                values=tuple(getattr(bar, name) for name in input_names),
            )
        )
    rows.sort(key=lambda row: (row.session, row.listing_id))
    if seen != {
        (day.session_date, listing.listing_id)
        for day in calendar.sessions
        for listing in listings
    }:
        raise ValueError(
            "Every configured listing/session must be retained; no fill/drop"
        )
    for listing in listings:
        if (
            listing.asset_type != "equity"
            or listing.delisted_on is not None
            or (
                listing.listed_on is not None
                and listing.listed_on > calendar.sessions[0].session_date
            )
        ):
            raise ValueError("Fixed full-range equity listing lifetime required")
    inventory = tuple((name, hashes[name]) for name in ARTIFACTS)
    policy_hash = digest(
        {
            "policy": policy.model_dump(mode="json"),
            "manifest_sha256": manifest_sha256,
            "artifacts": dict(inventory),
            "calendar_sha256": calendar.sha256,
        }
    )
    membership = {
        "policy": policy.membership,
        "listing_ids": tuple(sorted(ids)),
        "available_at": calendar.sessions[0].open_time.isoformat(),
        "source_policy_sha256": policy_hash,
    }
    universe = CalculationUniverse(
        content_sha256=digest(membership),
        listing_ids=tuple(sorted(ids)),
        membership_sha256=digest(membership),
        membership_available_at=calendar.sessions[0].open_time,
        membership_history="declared",
    )
    fields = tuple(
        InputField(
            name=name,
            unit="shares" if name == "volume" else listings[0].currency,
            price_semantics="not_price" if name == "volume" else "unadjusted",
        )
        for name in input_names
    )
    dataset = DatasetBinding(
        dataset_version_id=dataset_id,
        content_sha256=manifest_sha256,
        input_files_sha256=digest(dict(inventory)),
        price_semantics="unadjusted",
        availability_revision_sha256=policy_hash,
        source_quality="declared_source",
    )
    return ConfiguredSource(
        dataset,
        universe,
        calendar,
        fields,
        tuple(rows),
        digest(policy),
        inventory,
        request.start,
        request.end,
    )
