"""Actual retained-byte reconstruction with declared rows; not stopped runtime proof."""

import hashlib
import json

import pytest

from backend.app.structured_factor_reconstruction import reconstruct_input
from packages.factors.contracts import canonical_bytes, digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.structured_factor_strategy.assembly import assemble_input
from packages.structured_factor_strategy.wire import write_input
from tests.structured import test_services


@pytest.fixture
def retained_declaration(monkeypatch):
    args = test_services.encoded_structured_market()
    monkeypatch.setattr(test_services, "encoded_structured_market", lambda: args)
    material = test_services.material()
    snapshot = material["snapshot"]
    rows_raw = canonical_bytes(
        {
            "schema_version": "mineagent.execution-feature-rows.v1",
            "input_sha256": snapshot.input_sha256,
            "rows": [r.model_dump(mode="json") for r in material["rows"]],
        }
    )
    sha = hashlib.sha256(rows_raw).hexdigest()
    obj = snapshot.assembled_rows.model_dump(mode="json")
    obj.update(
        sha256=sha,
        size_bytes=len(rows_raw),
        storage_key=f"factors/{snapshot.owner_id}/objects/{sha}.json",
    )
    snapshot = ExecutionSnapshotManifest.model_validate(
        {**snapshot.model_dump(mode="json"), "assembled_rows": obj}
    )
    material["snapshot"] = snapshot
    value = assemble_input(**material)
    raw = write_input(value)
    config = material["config"]
    owner = str(value.owner_id)
    run_id = str(value.run_id)
    program = material["program"]
    version = {
        "id": str(value.structured_strategy_version_id),
        "owner_id": owner,
        "program": program.model_dump(mode="json"),
        "program_canonical": canonical_bytes(program).decode(),
        "program_sha256": digest(program),
    }
    spec = {
        "owner_id": owner,
        "run_id": run_id,
        "structured_strategy_version_id": version["id"],
        "config": config.model_dump(mode="json"),
        "config_canonical": canonical_bytes(config).decode(),
        "config_sha256": digest(config),
    }
    dataset = {
        "id": str(config.dataset_version_id),
        "owner_id": owner,
        "content_sha256": config.dataset_sha256,
        "storage_key": f"datasets/{owner}/{config.dataset_version_id}",
        "market": "US",
        "manifest": json.loads(args["manifest_bytes"]),
        "start_date": str(material["source"].source_start),
        "end_date": str(material["source"].source_end),
    }
    run = {
        "id": run_id,
        "owner_id": owner,
        "purpose": "factor_strategy",
        "engine": "structured",
        "generation": 1,
        "strategy_version_id": None,
        "config": spec["config"],
        "request_sha256": spec["config_sha256"],
        "dataset_version_id": dataset["id"],
    }
    execution = {
        "id": str(value.execution_id),
        "owner_id": owner,
        "run_id": run_id,
        "attempt_id": str(value.attempt_id),
        "generation": 1,
        "ordinal": 0,
        "structured_strategy_version_id": version["id"],
        "snapshot_id": str(snapshot.snapshot_id),
        "factor_version_id": str(snapshot.input.version.factor_version_id),
        "strategy_input": value.model_dump(mode="json"),
        "strategy_input_canonical": raw.decode(),
        "strategy_input_sha256": hashlib.sha256(raw).hexdigest(),
        "strategy_input_size_bytes": len(raw),
        "market_sha256": value.market_sha256,
        "market_size_bytes": len(material["market"].content),
    }
    publication = {
        "id": execution["snapshot_id"],
        "owner_id": owner,
        "factor_version_id": execution["factor_version_id"],
        "manifest": snapshot.model_dump(mode="json"),
        "manifest_sha256": digest(snapshot),
    }
    files = {
        dataset["storage_key"] + "/manifest.json": args["manifest_bytes"],
        obj["storage_key"]: rows_raw,
    }
    files.update(
        {dataset["storage_key"] + "/" + k: v for k, v in args["artifacts"].items()}
    )

    def read_bytes(key, maximum):
        raw = files[key]
        assert len(raw) <= maximum
        return raw

    return (
        {
            "run": run,
            "spec": spec,
            "version": version,
            "execution": execution,
            "publication": publication,
            "dataset": dataset,
            "read_bytes": read_bytes,
        },
        files,
        value,
    )


def test_rebuilds_exact_input_from_retained_files_without_staged_mounts(
    retained_declaration,
):
    kwargs, files, expected = retained_declaration
    actual, market, *_ = reconstruct_input(**kwargs)
    assert actual == expected
    assert hashlib.sha256(market).hexdigest() == expected.market_sha256
    assert not any("research-processes/" in k for k in files)


@pytest.mark.parametrize(
    "suffix",
    ["preview.json", "sessions.json", "actions.json", "corporate-action-coverage.json"],
)
def test_rejects_changed_retained_artifact(retained_declaration, suffix):
    kwargs, files, _ = retained_declaration
    files[kwargs["dataset"]["storage_key"] + "/" + suffix] += b" "
    with pytest.raises(ValueError):
        reconstruct_input(**kwargs)


def test_rejects_changed_rows_even_when_metadata_stays_identical(retained_declaration):
    kwargs, files, expected = retained_declaration
    files[expected.snapshot.assembled_rows.storage_key] += b" "
    with pytest.raises(ValueError, match="row commitment"):
        reconstruct_input(**kwargs)


def test_rejects_another_owner_before_reading(retained_declaration):
    kwargs, _, _ = retained_declaration
    kwargs["run"]["owner_id"] = "00000000-0000-0000-0000-000000000001"
    kwargs["read_bytes"] = lambda *_: pytest.fail(
        "foreign owner must fail before reads"
    )
    with pytest.raises(ValueError, match="run identity"):
        reconstruct_input(**kwargs)
