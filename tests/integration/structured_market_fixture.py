"""New valid-OHLC synthetic fixture; frozen022 close-only fixture is unchanged."""

import hashlib

from rd_configured_dataset_fixture import retained_marketdata_fixture

from packages.marketdata.normalize import canonical_json
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.structured_factor_strategy.market import GENERATOR


def encoded_structured_market():
    value = retained_marketdata_fixture()
    for row in value["bars"]:
        row["low"] = 90.0
    aaa = next(
        x["listing_id"] for x in value["manifest"]["listings"] if x["symbol"] == "AAA"
    )
    for row in value["bars"]:
        if row["listing_id"] == aaa and row["session_date"] == "2024-03-12":
            row["close"] = 103.0
    manifest = value["manifest"]
    manifest["price_semantics"]["algorithm_version"] = GENERATOR
    actions = canonical_json([])
    coverage = {
        "schema_version": "mineagent.structured-synthetic-actions.v1",
        "generator": GENERATOR,
        "start": manifest["request"]["start"],
        "end": manifest["request"]["end"],
        "listing_ids": sorted(x["listing_id"] for x in manifest["listings"]),
        "actions_sha256": hashlib.sha256(actions).hexdigest(),
        "qualification": "none_by_synthetic_construction_not_provider_evidence",
    }
    artifacts = {
        "preview.json": canonical_json(value["bars"]),
        "sessions.json": canonical_json(value["schedule"]),
        "actions.json": actions,
        "corporate-action-coverage.json": canonical_json(coverage),
    }
    manifest.update(
        artifact_files={k: hashlib.sha256(v).hexdigest() for k, v in artifacts.items()},
        bars_sha256=hashlib.sha256(artifacts["preview.json"]).hexdigest(),
        row_count=len(value["bars"]),
        structured_synthetic_provenance=coverage,
    )
    manifest["content_hash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    raw = canonical_json(manifest)
    return {
        "dataset_id": value["dataset_id"],
        "manifest_bytes": raw,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "artifacts": artifacts,
        "policy": ConfiguredSourcePolicy(acknowledged_unverified=True),
        "input_names": ("close", "volume"),
    }
