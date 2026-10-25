import hashlib
import json
from datetime import timedelta
from uuid import UUID

import pytest

from packages.factors.contracts import SnapshotInput
from packages.factors.fixtures import demo_fixture, demo_inputs
from packages.rdagent_adapter.prefix import (
    MarketObservation,
    PrefixManifest,
    project_prefix,
)


def inputs():
    snapshot = demo_fixture(
        factor_version_id=UUID(int=1), dataset_version_id=UUID(int=2)
    ).manifest.input
    calendar, data = demo_inputs()
    closes = {s.session_date: s.close_time for s in calendar.sessions}
    rows = tuple(
        MarketObservation(
            listing_id=r.listing_id,
            session=r.session_date,
            event_time=closes[r.session_date],
            available_at=r.available_at,
            values=(r.close,),
        )
        for r in data
    )
    session = calendar.sessions[3]
    args = {
        "host_cutoff": session.close_time,
        "output_event_time": session.close_time,
        "output_session": session.session_date,
        "calendar_sessions": tuple(s.session_date for s in calendar.sessions),
        "observations": rows,
        "candidate_image": "sha256:" + "a" * 64,
        "normalizer_image": "sha256:" + "b" * 64,
        "lag_available_at": session.close_time,
    }
    return snapshot, args


def test_exact_prefix_excludes_future_and_older_history():
    snapshot, args = inputs()
    manifest, files = project_prefix(snapshot, **args)
    rows = json.loads(files["observations.json"])
    assert len(rows) == 9
    assert {r["session"] for r in rows} == {
        d.isoformat() for d in args["calendar_sessions"][1:4]
    }
    assert manifest.dependency_claim == "provided_not_proven_read"
    assert PrefixManifest.model_validate_json(manifest.model_dump_json()) == manifest
    for item in manifest.input_files:
        assert item.sha256 == hashlib.sha256(files[item.name]).hexdigest()
    _, files2 = project_prefix(
        snapshot, **{**args, "observations": tuple(reversed(args["observations"]))}
    )
    assert files == files2


def test_delayed_peer_remains_in_full_universe_without_future_value():
    snapshot, args = inputs()
    rows = list(args["observations"])
    target = next(i for i, r in enumerate(rows) if r.session == args["output_session"])
    raw = rows[target].model_dump(mode="json")
    raw["available_at"] = args["host_cutoff"] + timedelta(days=1)
    rows[target] = MarketObservation.model_validate(raw)
    manifest, files = project_prefix(snapshot, **{**args, "observations": tuple(rows)})
    assert len(manifest.listings) == 3 and manifest.observation_count == 8
    assert len(json.loads(files["listings.json"])) == 3


@pytest.mark.parametrize(
    "change",
    [
        "future_event",
        "unready_membership",
        "duplicate",
        "unknown_listing",
        "field_shape",
        "bad_calendar",
        "outside_window",
        "short_cap",
    ],
)
def test_prefix_rejects_scope_substitution(change):
    snapshot, args = inputs()
    if change == "future_event":
        args["output_event_time"] += timedelta(seconds=1)
    elif change == "unready_membership":
        raw = snapshot.model_dump(mode="json")
        raw["calculation_universe"]["membership_available_at"] = args[
            "host_cutoff"
        ] + timedelta(seconds=1)
        snapshot = SnapshotInput.model_validate(raw)
    elif change == "duplicate":
        args["observations"] += args["observations"][:1]
    elif change in ("unknown_listing", "field_shape"):
        raw = args["observations"][0].model_dump(mode="json")
        raw.update(
            {"listing_id": "outside"}
            if change == "unknown_listing"
            else {"values": [1, 2]}
        )
        args["observations"] = (MarketObservation.model_validate(raw),) + args[
            "observations"
        ][1:]
    elif change == "bad_calendar":
        args["calendar_sessions"] = tuple(reversed(args["calendar_sessions"]))
    elif change == "outside_window":
        args["output_session"] += timedelta(days=500)
    elif change == "short_cap":
        args["max_output_rows"] = 2
    with pytest.raises(ValueError):
        project_prefix(snapshot, **args)


def test_changed_cutoff_and_image_have_distinct_cache_identity():
    snapshot, args = inputs()
    first, _ = project_prefix(snapshot, **args)
    changed, _ = project_prefix(
        snapshot, **{**args, "host_cutoff": args["host_cutoff"] + timedelta(seconds=1)}
    )
    assert first.sha256 != changed.sha256
    changed, _ = project_prefix(
        snapshot, **{**args, "candidate_image": "sha256:" + "c" * 64}
    )
    assert first.sha256 != changed.sha256


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf")])
def test_observation_refuses_non_numeric_or_nonfinite(value):
    _, args = inputs()
    raw = args["observations"][0].model_dump(mode="json")
    raw["values"] = [value]
    with pytest.raises(ValueError):
        MarketObservation.model_validate(raw)


def test_host_lag_readiness_is_not_backdated_and_can_delay_publication():
    snapshot, args = inputs()
    with pytest.raises(ValueError, match="Lag readiness"):
        project_prefix(
            snapshot,
            **{**args, "lag_available_at": args["host_cutoff"] - timedelta(seconds=1)},
        )
    late = args["host_cutoff"] + timedelta(days=1)
    manifest, _ = project_prefix(snapshot, **{**args, "lag_available_at": late})
    assert manifest.publication_ready_at == late


def test_future_fitted_transform_and_oversized_input_fail_closed():
    snapshot, args = inputs()
    raw = snapshot.model_dump(mode="json")
    raw["definition"]["preprocessing"] = {
        "kind": "fitted",
        "transform_id": "frozen-standardizer",
        "code_sha256": "a" * 64,
        "parameters_sha256": "b" * 64,
        "fitted_artifact_sha256": "c" * 64,
        "fit_window": {"start": "2024-01-01", "end": "2024-01-02"},
        "last_input_at": args["host_cutoff"].isoformat(),
        "available_at": (args["host_cutoff"] + timedelta(seconds=1)).isoformat(),
    }
    from packages.factors.contracts import FactorDefinition

    raw["version"]["definition_sha256"] = FactorDefinition.model_validate(
        raw["definition"]
    ).sha256
    with pytest.raises(ValueError, match="fitted"):
        project_prefix(SnapshotInput.model_validate(raw), **args)
    with pytest.raises(ValueError, match="row limit"):
        project_prefix(
            snapshot, **{**args, "observations": args["observations"][:1] * 640001}
        )


def test_prefix_v2_preserves_shared_lookback_two_prior_plus_current_numeric_meaning():
    snapshot, args = inputs()
    manifest, files = project_prefix(snapshot, **args)
    assert manifest.schema_version == "mineagent.factor-prefix-input.v2"
    assert manifest.history_semantics == "prior_sessions_plus_current"
    assert (
        manifest.lookback_prior_sessions == 2 and len(manifest.provided_sessions) == 3
    )
    observations = json.loads(files["observations.json"])
    expected = demo_fixture(
        factor_version_id=UUID(int=1),
        dataset_version_id=UUID(int=2),
        lookback_sessions=2,
    )
    for listing in manifest.listings:
        prices = [
            row["values"][0]
            for row in observations
            if row["listing_code"] == listing.code
        ]
        actual = prices[-1] / prices[0] - 1
        row = next(
            row
            for row in expected.rows
            if row.session_date == args["output_session"]
            and row.listing_id == listing.listing_id
        )
        assert actual == row.value
    old = manifest.model_dump(mode="json")
    old["schema_version"] = "mineagent.factor-prefix-input.v1"
    with pytest.raises(ValueError):
        PrefixManifest.model_validate(old)


def test_prior_history_maximum_63_mounts_64_and_64_is_rejected():
    from packages.factors.contracts import FactorDefinition

    snapshot, args = inputs()
    raw = snapshot.model_dump(mode="json")
    raw["definition"]["lookback_sessions"] = 63
    raw["version"]["definition_sha256"] = FactorDefinition.model_validate(
        raw["definition"]
    ).sha256
    snapshot = SnapshotInput.model_validate(raw)
    calendar = tuple(
        args["output_session"] - timedelta(days=offset) for offset in range(63, -1, -1)
    )
    manifest, _ = project_prefix(
        snapshot, **{**args, "calendar_sessions": calendar, "observations": ()}
    )
    assert (
        len(manifest.provided_sessions) == 64 and manifest.lookback_prior_sessions == 63
    )
    raw["definition"]["lookback_sessions"] = 64
    raw["version"]["definition_sha256"] = FactorDefinition.model_validate(
        raw["definition"]
    ).sha256
    with pytest.raises(ValueError, match="63 prior"):
        project_prefix(SnapshotInput.model_validate(raw), **args)
