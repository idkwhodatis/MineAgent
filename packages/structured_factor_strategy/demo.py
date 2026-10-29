"""Small deterministic synthetic panel for the normal owned research workflow.

Prices and absence of corporate actions are constructed, not market observations
or PIT evidence. Four sessions are a plumbing demo, not meaningful validation.
No database identities, source code, factor values, results or grants are minted.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid5

from packages.factors.contracts import DateWindow
from packages.marketdata.contracts import Bar, BarsRequest, PriceSemantics, make_listing
from packages.marketdata.normalize import canonical_json
from packages.structured_factor_strategy.market import GENERATOR, stage_market

START = date(2024, 3, 11)
END = date(2024, 3, 15)
WINDOW = DateWindow(start=date(2024, 3, 13), end=END)
RD_WINDOW = DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 14))
SOURCE = "mineagent_structured_synthetic_demo"
DEMO_SCHEMA = "mineagent.structured-demo-dataset.v1"
DEMO_MARKER = {
    "schema_version": DEMO_SCHEMA,
    "generator": GENERATOR,
    "qualification": "synthetic_by_construction_not_live_or_pit_evidence",
    "listings": 3,
    "sessions": 4,
}
QUALIFICATION = "synthetic_by_construction_not_live_or_pit_evidence"


def dataset_id(owner_id):
    if type(owner_id) is not UUID:
        raise ValueError("Exact owner UUID required")
    return uuid5(owner_id, DEMO_SCHEMA + ":" + GENERATOR)


@dataclass(frozen=True)
class DemoMarket:
    manifest_bytes: bytes
    artifacts: tuple[tuple[str, bytes], ...]

    @property
    def sha256(self):
        return hashlib.sha256(self.manifest_bytes).hexdigest()

    @property
    def files(self):
        return (*self.artifacts, ("manifest.json", self.manifest_bytes))

    @property
    def peak_bytes(self):
        # Atomic publisher may retain a complete temporary plus final inode.
        return 2 * sum(len(raw) for _, raw in self.files)

    @property
    def peak_files(self):
        # Owner/datasets/version directories, five final and one temporary file.
        return 9


def encoded_demo_market():
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
        listings=listings, start=START, end=END, as_of=datetime(2024, 3, 20, tzinfo=UTC)
    )
    # Captured by the real pinned data-runtime session_schedule, not a weekday
    # approximation. The API intentionally has no exchange-calendars dependency.
    calendar_bytes = Path(__file__).with_name("demo_schedule.json").read_bytes()
    if (
        hashlib.sha256(calendar_bytes).hexdigest()
        != "ca5bd13db2979c4de16338caf6be530c05ce1abf6f957ee3accbe4c2d9af2eda"
    ):
        raise ValueError("Reviewed real-calendar artifact changed")
    schedule = json.loads(calendar_bytes)
    if canonical_json(schedule) != calendar_bytes:
        raise ValueError("Canonical calendar artifact required")
    closes = {
        "AAA": (100, 103, 104, 110),
        "BBB": (100, 101, 102, 104),
        "CCC": (100, 99, 98, 96),
    }
    bars = [
        Bar(
            listing_id=listing.listing_id,
            session_date=date(2024, 3, day),
            open=100,
            high=130,
            low=90,
            close=closes[listing.symbol][day - 11],
            volume=1000,
            currency="USD",
            volume_unit="shares",
        ).model_dump(mode="json")
        for day in (11, 12, 13, 14)
        for listing in listings
    ]
    semantics = PriceSemantics(
        native_mode="raw",
        split_basis="unadjusted",
        dividend_basis="unadjusted",
        algorithm_version=GENERATOR,
    )
    actions = canonical_json([])
    coverage = {
        "schema_version": "mineagent.structured-synthetic-actions.v1",
        "generator": GENERATOR,
        "start": str(START),
        "end": str(END),
        "listing_ids": sorted(x.listing_id for x in listings),
        "actions_sha256": hashlib.sha256(actions).hexdigest(),
        "qualification": "none_by_synthetic_construction_not_provider_evidence",
    }
    artifacts = {
        "preview.json": canonical_json(bars),
        "sessions.json": canonical_json(schedule),
        "actions.json": actions,
        "corporate-action-coverage.json": canonical_json(coverage),
    }
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
        "row_count": len(bars),
        "bars_sha256": hashlib.sha256(artifacts["preview.json"]).hexdigest(),
        "artifact_files": {
            k: hashlib.sha256(v).hexdigest() for k, v in artifacts.items()
        },
        "structured_synthetic_provenance": coverage,
        "structured_demo": dict(DEMO_MARKER),
    }
    manifest["content_hash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    result = DemoMarket(canonical_json(manifest), tuple(sorted(artifacts.items())))
    # Real strict parser rejects unavailable/wrong calendar versions and any drift.
    stage_market(
        result.manifest_bytes,
        dict(result.artifacts),
        dataset_sha256=result.sha256,
        window=WINDOW,
    )
    return result


def rd_preset():
    """Explicit compatible window; never silently reuse a larger default form."""
    return {
        "window": RD_WINDOW.model_dump(mode="json"),
        "lookback_sessions": 2,
        "lag_sessions": 0,
        "input_fields": ["close", "volume"],
        "max_iterations": 1,
        "acknowledgement_required": True,
        "task": {
            "name": "tiny-demo-close-return",
            "description": "Synthetic plumbing demonstration only, not a market signal",
            "formulation": "close[t]/close[t-2]-1",
            "factor_kind": "time_series",
            "output_unit": "return",
            "interpretation": "unsigned",
        },
        "objective": {
            "window": RD_WINDOW.model_dump(mode="json"),
            "label_horizon_sessions": 1,
            "metric": "ic",
            "direction": "at_least",
            "threshold": 0.0,
            "minimum_defined_days": 1,
            "minimum_pairs": 2,
        },
        "qualification": QUALIFICATION,
    }


def reproduce_calendar_artifact():
    """Offline data-runtime verification; never invoked by the API publisher."""
    from packages.marketdata.calendars import session_schedule

    bundle = encoded_demo_market()
    request = BarsRequest.model_validate(json.loads(bundle.manifest_bytes)["request"])
    raw = canonical_json(session_schedule(request))
    if raw != dict(bundle.artifacts)["sessions.json"]:
        raise ValueError("Pinned data-runtime schedule differs from retained artifact")
    return raw
