import hashlib
from pathlib import Path

import pytest

from packages.factors.contracts import ExposureHistory
from packages.factors.fixtures import demo_inputs
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    NORMALIZER_LOCK_SHA256,
    NUMERIC_CHILD_ENV,
    candidate_inputs,
    normalizer_inputs,
    numeric_abi_description,
)
from packages.rdagent_adapter.prefix import MarketObservation, project_prefix
from runtimes.rdagent.probe_fixture import research_dict


def projected():
    from uuid import UUID

    config = FactorResearchConfig.model_validate(research_dict())
    calendar, prices = demo_inputs()
    source = b"pass\n"
    sha = hashlib.sha256(source).hexdigest()
    snapshot = research_snapshot_input(
        config,
        factor_version_id=UUID(int=1),
        code_sha256=sha,
        exposure_history=ExposureHistory(),
    )
    dates = {day.session_date: day for day in calendar.sessions}
    observations = tuple(
        MarketObservation(
            listing_id=row.listing_id,
            session=row.session_date,
            event_time=dates[row.session_date].close_time,
            available_at=row.available_at,
            values=(row.close,),
        )
        for row in prices
    )
    day = [
        day
        for day in calendar.sessions
        if snapshot.window.start <= day.session_date < snapshot.window.end
    ][-1]
    prefix, files = project_prefix(
        snapshot,
        host_cutoff=day.close_time,
        output_event_time=day.close_time,
        output_session=day.session_date,
        calendar_sessions=tuple(dates),
        observations=observations,
        candidate_image=config.runtime.candidate_image,
        normalizer_image=config.runtime.normalizer_image,
        lag_available_at=day.close_time,
        max_output_rows=config.max_output_rows_per_cutoff,
        max_output_bytes=config.max_output_bytes_per_cutoff,
    )
    return config, snapshot, prefix, files, source, sha


def test_candidate_and_normalizer_inventory_are_fixed_and_no_native_parse():
    _, snapshot, prefix, files, source, sha = projected()
    inventory, inputs, commitment = candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=source,
        expected_source_sha256=sha,
        projected_files=files,
    )
    assert [item.destination for item in inventory] == [
        "/input/listings.json",
        "/input/manifest.json",
        "/input/observations.json",
        "/input/source.py",
    ]
    assert inputs["/input/source.py"] == source and commitment
    # A hash-only host stage does not parse arbitrary native bytes or call them valid.
    native = b"not HDF; parser must reject later"
    refs, _, raw = normalizer_inputs(
        prefix,
        hdf=native,
        expected_hdf_sha256=hashlib.sha256(native).hexdigest(),
        listings=files["listings.json"],
    )
    assert [item.destination for item in refs] == [
        "/input/listings.json",
        "/input/manifest.json",
        "/input/result.h5",
    ]
    assert b'"ipc_policy":"none"' in raw


@pytest.mark.parametrize("bad", ["source", "extra", "bytes", "snapshot"])
def test_numeric_inventory_rejects_source_input_or_snapshot_drift(bad):
    _, snapshot, prefix, files, source, sha = projected()
    if bad == "source":
        source += b"# changed"
    elif bad == "extra":
        files = {**files, "secret.json": b"x"}
    elif bad == "bytes":
        files = {**files, "observations.json": b"[]"}
    else:
        raw = snapshot.model_dump(mode="json")
        raw["environment_sha256"] = "0" * 64
        snapshot = type(snapshot).model_validate(raw)
    with pytest.raises(ValueError):
        candidate_inputs(
            prefix,
            snapshot_input=snapshot,
            source=source,
            expected_source_sha256=sha,
            projected_files=files,
        )


def test_numeric_lock_and_child_environment_are_explicit_not_image_env_assumptions():
    root = Path(__file__).resolve().parents[2]
    assert (
        hashlib.sha256(
            (root / "runtimes/factor_execution/requirements.lock").read_bytes()
        ).hexdigest()
        == CANDIDATE_LOCK_SHA256
    )
    assert (
        hashlib.sha256(
            (root / "runtimes/factor_normalizer/requirements.lock").read_bytes()
        ).hexdigest()
        == NORMALIZER_LOCK_SHA256
    )
    env = dict(NUMERIC_CHILD_ENV)
    assert env["OPENBLAS_NUM_THREADS"] == env["OMP_NUM_THREADS"] == "1"
    assert env["HDF5_PLUGIN_PRELOAD"] == "::"
    description = numeric_abi_description(projected()[0])
    assert "/input/source.py" in description and "/output/result.h5" in description
    assert "No links" in description and "L prior sessions plus current" in description
