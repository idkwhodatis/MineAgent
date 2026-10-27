"""Actual retained bytes/parser tests, not native engine or database acceptance."""

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "integration"))
from structured_market_fixture import encoded_structured_market

from packages.factors.contracts import DateWindow
from packages.marketdata.normalize import canonical_json
from packages.rdagent_adapter.configured_source import ARTIFACTS, parse_retained_source
from packages.structured_factor_strategy.market import stage_market

WINDOW = DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 15))


def stage(args):
    return stage_market(
        args["manifest_bytes"],
        args["artifacts"],
        dataset_sha256=args["manifest_sha256"],
        window=WINDOW,
    )


def rebind(args, name, value):
    args["artifacts"][name] = canonical_json(value)
    manifest = json.loads(args["manifest_bytes"])
    manifest["artifact_files"][name] = hashlib.sha256(
        args["artifacts"][name]
    ).hexdigest()
    if name == "preview.json":
        manifest["bars_sha256"] = manifest["artifact_files"][name]
    manifest.pop("content_hash")
    manifest["content_hash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    args["manifest_bytes"] = canonical_json(manifest)
    args["manifest_sha256"] = hashlib.sha256(args["manifest_bytes"]).hexdigest()


def test_real_bound_market_and_factor_source_share_exact_dataset():
    args = encoded_structured_market()
    market = stage(args)
    source_args = dict(args, artifacts={k: args["artifacts"][k] for k in ARTIFACTS})
    source = parse_retained_source(**source_args)
    assert source.dataset.content_sha256 == args["manifest_sha256"]
    assert {x.listing_id for x in market.listings} == set(source.universe.listing_ids)
    raw = json.loads(market.content)
    assert len(raw["bars"]) == 9
    assert [c["session_date"] for c in raw["sessions"]] == [
        "2024-03-12",
        "2024-03-13",
        "2024-03-14",
    ]
    assert raw["corporate_actions"] == []
    assert market.qualification == "synthetic_by_construction_not_live_or_pit_evidence"


@pytest.mark.parametrize(
    "kind", ["low", "currency", "flags", "missing", "duplicate", "unknown"]
)
def test_recommitted_invalid_bars_reject(kind):
    args = encoded_structured_market()
    bars = json.loads(args["artifacts"]["preview.json"])
    if kind == "low":
        bars[-1]["low"] = 99.0
    elif kind == "currency":
        bars[0]["currency"] = "EUR"
    elif kind == "flags":
        bars[0]["flags"] = ["unknown"]
    elif kind == "missing":
        bars.pop()
    elif kind == "duplicate":
        bars[-1] = bars[0]
    elif kind == "unknown":
        bars[0]["listing_id"] = "00000000-0000-0000-0000-000000000000"
    rebind(args, "preview.json", bars)
    with pytest.raises(ValueError):
        stage(args)


def test_empty_actions_without_exact_coverage_is_not_authority():
    args = encoded_structured_market()
    coverage = json.loads(args["artifacts"]["corporate-action-coverage.json"])
    coverage["listing_ids"].pop()
    rebind(args, "corporate-action-coverage.json", coverage)
    with pytest.raises(ValueError, match="coverage"):
        stage(args)


def test_actions_not_silently_ignored():
    args = encoded_structured_market()
    rebind(args, "actions.json", [{"action_type": "split"}])
    with pytest.raises(ValueError, match="coverage"):
        stage(args)


def test_tamper_rejects_before_staging():
    args = encoded_structured_market()
    args["artifacts"]["preview.json"] += b" "
    with pytest.raises(ValueError, match="identity"):
        stage(args)
