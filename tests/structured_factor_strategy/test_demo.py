"""Actual canonical source and parser tests; no DB/runtime acceptance claim."""

import hashlib
import json
from datetime import date
from uuid import uuid4

import pytest

from packages.factors.contracts import DateWindow
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)
from packages.structured_factor_strategy.demo import (
    DEMO_MARKER,
    RD_WINDOW,
    WINDOW,
    dataset_id,
    encoded_demo_market,
    rd_preset,
)
from packages.structured_factor_strategy.market import ARTIFACTS, stage_market


@pytest.fixture(scope="module")
def demo():
    return encoded_demo_market()


def test_generator_deterministic_explicit_synthetic_and_actual_calendar(demo):
    assert demo == encoded_demo_market()
    manifest = json.loads(demo.manifest_bytes)
    assert manifest["structured_demo"] == DEMO_MARKER
    assert manifest["is_synthetic"] is True
    assert manifest["universe"] == {"state": "user_fixed", "point_in_time": False}
    assert set(dict(demo.artifacts)) == ARTIFACTS
    assert (
        hashlib.sha256(dict(demo.artifacts)["sessions.json"]).hexdigest()
        == "ca5bd13db2979c4de16338caf6be530c05ce1abf6f957ee3accbe4c2d9af2eda"
    )

    bars = json.loads(dict(demo.artifacts)["preview.json"])
    assert len(bars) == 12
    assert all(
        row["low"]
        <= min(row["open"], row["close"])
        <= max(row["open"], row["close"])
        <= row["high"]
        for row in bars
    )
    aaa = next(x["listing_id"] for x in manifest["listings"] if x["symbol"] == "AAA")
    assert (
        next(
            row["close"]
            for row in bars
            if row["listing_id"] == aaa and row["session_date"] == "2024-03-12"
        )
        == 103
    )
    assert json.loads(dict(demo.artifacts)["actions.json"]) == []
    assert (
        json.loads(dict(demo.artifacts)["corporate-action-coverage.json"])[
            "qualification"
        ]
        == "none_by_synthetic_construction_not_provider_evidence"
    )
    assert demo.files[-1][0] == "manifest.json"


def test_real_rd_and_structured_readers_accept_same_immutable_dataset(demo):
    source = parse_retained_source(
        dataset_id=uuid4(),
        manifest_bytes=demo.manifest_bytes,
        manifest_sha256=demo.sha256,
        artifacts={
            k: v for k, v in demo.artifacts if k in {"preview.json", "sessions.json"}
        },
        policy=ConfiguredSourcePolicy(acknowledged_unverified=True),
        input_names=("close", "volume"),
    )
    days = [x.session_date for x in source.calendar.sessions]
    assert days == [date(2024, 3, d) for d in (11, 12, 13, 14)]
    assert days.index(RD_WINDOW.start) == 2 and days[-1] >= RD_WINDOW.end
    staged = stage_market(
        demo.manifest_bytes,
        dict(demo.artifacts),
        dataset_sha256=demo.sha256,
        window=WINDOW,
    )
    assert len(staged.listings) == 3
    preset = rd_preset()
    assert DateWindow.model_validate(preset["window"]) == RD_WINDOW
    assert preset["lookback_sessions"] == 2 and preset["lag_sessions"] == 0


def test_owner_ids_are_deterministic_separate_and_not_content_hash_cycle():
    one, two = uuid4(), uuid4()
    assert dataset_id(one) == dataset_id(one) != dataset_id(two)
    with pytest.raises(ValueError):
        dataset_id(str(one))


@pytest.mark.parametrize("name", sorted(ARTIFACTS))
def test_each_exact_artifact_hash_is_required(demo, name):
    files = dict(demo.artifacts)
    files[name] += b" "
    with pytest.raises(ValueError):
        stage_market(
            demo.manifest_bytes, files, dataset_sha256=demo.sha256, window=WINDOW
        )


def test_manifest_hash_is_actual_retained_bytes(demo):
    assert demo.sha256 == hashlib.sha256(demo.manifest_bytes).hexdigest()
    assert 0 < sum(len(raw) for _, raw in demo.files) < 65536
