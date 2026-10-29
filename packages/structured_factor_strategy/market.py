"""Bounded synthetic research market staging, not a data or execution grant.

The initial example accepts explicit retained synthetic action evidence only.
A close-only factor source, empty action array, or synthetic flag alone is not
sufficient. Existing portable staging and real-upload semantics are unchanged.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from packages.factors.contracts import DateWindow
from packages.marketdata.contracts import Bar, BarsRequest
from packages.marketdata.normalize import canonical_json as marketdata_bytes
from packages.strategy_sdk import (
    BarView,
    JsonLimits,
    ListingView,
    canonical_json,
    parse_json,
)

LIMITS = JsonLimits(max_bytes=32 * 1024**2, max_nodes=2_000_000, max_items=100_000)
ARTIFACTS = frozenset(
    {"preview.json", "sessions.json", "actions.json", "corporate-action-coverage.json"}
)
GENERATOR = "mineagent.structured-synthetic-market.v1"


@dataclass(frozen=True)
class StagedStructuredMarket:
    content: bytes
    listings: tuple[ListingView, ...]
    sessions: tuple[dict, ...]
    qualification: str = "synthetic_by_construction_not_live_or_pit_evidence"


def stage_market(
    manifest_bytes,
    artifacts,
    *,
    dataset_sha256,
    window,
    declared_upload=False,
    dataset_id=None,
    owner_id=None,
):
    """Pure exact-byte parser; caller resolves owned dataset and exposure first."""
    window = DateWindow.model_validate(window)
    if not 1 <= (window.end - window.start).days <= 8:
        raise ValueError("Bounded structured execution window required")
    if (
        type(manifest_bytes) is not bytes
        or len(manifest_bytes) > 2 * 1024**2
        or hashlib.sha256(manifest_bytes).hexdigest() != dataset_sha256
    ):
        raise ValueError("Exact retained dataset manifest required")
    manifest = parse_json(manifest_bytes, limits=LIMITS)
    if type(manifest) is not dict or marketdata_bytes(manifest) != manifest_bytes:
        raise ValueError("Canonical retained manifest required")
    if type(declared_upload) is not bool:
        raise ValueError("Explicit declared-upload market dispatch required")
    if declared_upload and manifest.get("schema_version") == 3:
        from packages.rdagent_adapter.declared_projection import project_basket

        projected = project_basket(
            manifest_bytes,
            artifacts,
            dataset_id=dataset_id,
            owner_id=owner_id,
            manifest_sha256=dataset_sha256,
        )
        request = BarsRequest.model_validate(projected["request"])
        if not request.start <= window.start < window.end <= request.end:
            raise ValueError("Declared market window outside exact source")
        parsed = {
            name: parse_json(artifacts[name], limits=LIMITS)
            for name in ("preview.json", "sessions.json")
        }
        return _stage_market_rows(
            projected, artifacts, parsed, request, window, "user_declaration_unverified"
        )
    inner = dict(manifest)
    expected_inner = inner.pop("content_hash", None)
    if hashlib.sha256(marketdata_bytes(inner)).hexdigest() != expected_inner:
        raise ValueError("Dataset inner commitment changed")
    if (
        manifest.get("schema_version") != "marketdata.v1"
        or manifest.get("transformation_version") != "normalize.v1"
        or manifest.get("is_synthetic") is not True
        or manifest.get("data_evidence") != "synthetic_fixture_not_live"
    ):
        raise ValueError(
            "Initial structured example requires explicitly synthetic data"
        )
    request = BarsRequest.model_validate(manifest["request"])
    if (
        request.interval != "1d"
        or not request.start <= window.start < window.end <= request.end
    ):
        raise ValueError("Daily window outside retained dataset")
    if not 1 <= len(request.listings) <= 100 or manifest.get("listings") != [
        x.model_dump(mode="json") for x in request.listings
    ]:
        raise ValueError("Exact bounded listing inventory required")
    if manifest.get("universe") != {"state": "user_fixed", "point_in_time": False}:
        raise ValueError("Explicit non-PIT fixed basket required")
    semantics = manifest.get("price_semantics") or {}
    if (
        semantics.get("split_basis") != "unadjusted"
        or semantics.get("dividend_basis") != "unadjusted"
        or semantics.get("algorithm_version") != GENERATOR
    ):
        raise ValueError("Exact synthetic raw-price construction required")
    if (
        manifest.get("warnings")
        or (manifest.get("quality") or {}).get("unknown_flags")
        or (manifest.get("evaluation_compatibility") or {}).get("compatible") is False
    ):
        raise ValueError("Unresolved source quality")
    if type(artifacts) is not dict or set(artifacts) != ARTIFACTS:
        raise ValueError("Exact structured artifact inventory required")
    parsed = {}
    total = 0
    for name, raw in artifacts.items():
        if type(raw) is not bytes or not 0 < len(raw) <= LIMITS.max_bytes:
            raise ValueError("Bounded retained artifact required")
        total += len(raw)
        if total > 64 * 1024**2 or hashlib.sha256(raw).hexdigest() != (
            manifest.get("artifact_files") or {}
        ).get(name):
            raise ValueError("Retained artifact identity changed")
        parsed[name] = parse_json(raw, limits=LIMITS)
        if marketdata_bytes(parsed[name]) != raw:
            raise ValueError("Canonical retained artifact required")
    ids = sorted(x.listing_id for x in request.listings)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate listing identity")
    expected_coverage = {
        "schema_version": "mineagent.structured-synthetic-actions.v1",
        "generator": GENERATOR,
        "start": str(request.start),
        "end": str(request.end),
        "listing_ids": ids,
        "actions_sha256": hashlib.sha256(artifacts["actions.json"]).hexdigest(),
        "qualification": "none_by_synthetic_construction_not_provider_evidence",
    }
    if (
        parsed["actions.json"] != []
        or parsed["corporate-action-coverage.json"] != expected_coverage
        or manifest.get("structured_synthetic_provenance") != expected_coverage
    ):
        raise ValueError("Explicit exact synthetic action coverage required")
    return _stage_market_rows(
        manifest,
        artifacts,
        parsed,
        request,
        window,
        "synthetic_by_construction_not_live_or_pit_evidence",
    )


def _stage_market_rows(manifest, artifacts, parsed, request, window, qualification):
    """Common host market projection after separate, exact source verification.

    Clock-based availability and membership are declared research assumptions for
    real uploads, not independently proven historical/PIT knowledge.
    """
    ids = sorted(x.listing_id for x in request.listings)
    schedule = parsed["sessions.json"]
    if (
        type(schedule) is not dict
        or schedule.get("schema_version") != "marketdata.session-schedule.v1"
        or schedule.get("generator")
        != {"name": "exchange_calendars", "version": "4.13.2"}
        or (schedule.get("start"), schedule.get("end"))
        != (str(request.start), str(request.end))
    ):
        raise ValueError("Exact independently rechecked exchange schedule required")
    calendar = (schedule.get("calendars") or {}).get("XNYS") or {}
    clocks = calendar.get("sessions")
    if calendar.get("status") != "complete" or type(clocks) is not list or not clocks:
        raise ValueError("Complete exchange schedule required")
    dates = []
    for clock in clocks:
        if type(clock) is not dict or set(clock) != {
            "session_date",
            "open_time",
            "close_time",
            "next_open_time",
        }:
            raise ValueError("Closed exchange session required")
        dates.append(date.fromisoformat(clock["session_date"]))
    if dates != sorted(set(dates)) or not all(
        request.start <= d < request.end for d in dates
    ):
        raise ValueError("Ordered covered exchange sessions required")
    prior = [c for c in clocks if c["session_date"] < str(window.start)]
    active = [
        c for c in clocks if str(window.start) <= c["session_date"] < str(window.end)
    ]
    if not prior or not 2 <= len(active) <= 8:
        raise ValueError(
            "One prior warmup plus signal and later fill sessions required"
        )
    selected = prior[-1:] + active
    by_day = {c["session_date"]: c for c in selected}
    listings = []
    for source in sorted(request.listings, key=lambda x: x.listing_id):
        UUID(source.listing_id)
        binding = (schedule.get("listing_calendars") or {}).get(source.listing_id) or {}
        if (
            source.asset_type != "equity"
            or source.mic != "XNYS"
            or source.calendar != "XNYS"
            or source.currency != "USD"
            or source.timezone != "America/New_York"
            or source.delisted_on is not None
            or (source.listed_on and source.listed_on > request.start)
            or binding.get("mic") != "XNYS"
            or binding.get("calendar") != "XNYS"
            or binding.get("calendar_basis") != "exchange_calendar"
        ):
            raise ValueError("Initial exact US fixed equity profile required")
        listings.append(
            ListingView.model_validate(
                {
                    **{
                        k: getattr(source, k)
                        for k in (
                            "listing_id",
                            "instrument_id",
                            "symbol",
                            "mic",
                            "currency",
                            "timezone",
                            "calendar",
                        )
                    },
                    "effective_from": request.start,
                    "known_at": clocks[0]["open_time"],
                    "lot_size": 1,
                }
            )
        )
    raw_bars = parsed["preview.json"]
    if (
        type(raw_bars) is not list
        or not raw_bars
        or len(raw_bars) > 100_000
        or manifest.get("row_count") != len(raw_bars)
        or manifest.get("bars_sha256")
        != hashlib.sha256(artifacts["preview.json"]).hexdigest()
    ):
        raise ValueError("Exact bounded bar inventory required")
    seen, bars = set(), []
    for raw in raw_bars:
        bar = Bar.model_validate(raw)
        key = (bar.listing_id, str(bar.session_date))
        if (
            key in seen
            or bar.listing_id not in ids
            or bar.session_date not in dates
            or bar.currency != "USD"
            or bar.volume_unit != "shares"
            or bar.flags
            or not 0
            < bar.low
            <= min(bar.open, bar.close)
            <= max(bar.open, bar.close)
            <= bar.high
            or bar.volume < 0
        ):
            raise ValueError("Invalid, duplicate or unsupported OHLCV")
        seen.add(key)
        if str(bar.session_date) not in by_day:
            continue
        clock = by_day[str(bar.session_date)]
        bars.append(
            BarView.model_validate(
                {
                    **{
                        k: getattr(bar, k)
                        for k in (
                            "listing_id",
                            "session_date",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                        )
                    },
                    "completed_at": clock["close_time"],
                    "available_at": clock["close_time"],
                    "volume_unit": "shares",
                    "complete": True,
                }
            ).model_dump(mode="json")
        )
    if seen != {(i, str(d)) for i in ids for d in dates}:
        raise ValueError("No missing source bars may be filled or dropped")
    bars.sort(key=lambda b: (b["session_date"], b["listing_id"]))
    content = canonical_json(
        {
            "listings": [x.model_dump(mode="json") for x in listings],
            "bars": bars,
            "sessions": selected,
            "corporate_actions": [],
        },
        limits=LIMITS,
    )
    return StagedStructuredMarket(
        content, tuple(listings), tuple(selected), qualification
    )
