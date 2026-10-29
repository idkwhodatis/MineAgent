"""Shared immutable input reconstruction for cold/historical analysis reads.

Caller supplies already owner/scope-checked persisted rows and a bounded guarded
reader. This pure graph/byte check does not grant current execution authority.
"""

from uuid import UUID

from backend.app.factor_analysis_canonical import read_objective
from backend.app.rdagent_configured_source import derive_configured_labels
from backend.app.rdagent_snapshot_config import parse_snapshot_config
from packages.factors.contracts import FeatureRow, canonical_bytes, digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.marketdata.normalize import canonical_json as marketdata_bytes
from packages.qlib_adapter.direct_wire import DirectInput, write_input
from packages.qlib_adapter.execution_binding import bind_completed_snapshot
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)
from packages.strategy_sdk import JsonLimits, parse_json


def reconstruct_direct_input(
    *,
    plan,
    intent,
    publication,
    spec,
    dataset,
    read_bytes,
    provider_config=False,
    composed_controller=False,
    seeded_controller=False,
):
    """Return data-only complete input after actual retained dataset/row rereads."""
    objective = read_objective(plan)
    policy = ConfiguredSourcePolicy.model_validate(plan["source_policy"])
    config = parse_snapshot_config(
        spec["config"],
        owner_id=spec["owner_id"],
        provider_config=provider_config,
        composed_controller=composed_controller,
        seeded_controller=seeded_controller,
    )
    manifest = ExecutionSnapshotManifest.model_validate(publication["manifest"])
    owner = str(plan["owner_id"])
    did = str(plan["label_source_dataset_id"])
    expected_key = f"datasets/{owner}/{did}"
    if (
        str(intent["plan_id"]) != str(plan["id"])
        or str(intent["snapshot_id"]) != str(publication["id"])
        or str(intent["label_source_id"]) != did
        or str(dataset["id"]) != did
        or str(dataset["owner_id"]) != owner
        or dataset["storage_key"] != expected_key
        or str(plan["spec_id"]) != str(spec["id"])
        or str(spec["owner_id"]) != owner
        or str(spec["run_id"]) != str(plan["run_id"])
        or plan["config_sha256"] != config.sha256
        or plan["input_sha256"] != config.input.sha256
        or plan["objective_sha256"] != objective.sha256
        or plan["source_policy_sha256"] != digest(policy)
        or dataset["content_sha256"] != plan["label_source_manifest_sha256"]
        or str(config.input.dataset.dataset_version_id) != did
        or config.input.dataset.content_sha256 != dataset["content_sha256"]
        or objective.window != config.input.window
        or manifest.input.window != config.input.window
        or manifest.input.dataset != config.input.dataset
        or publication["manifest_sha256"] != digest(manifest)
        or manifest.snapshot_id != UUID(str(publication["id"]))
        or any(
            str(intent[k]) != str(publication[k])
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        )
    ):
        raise ValueError("Analysis retained graph differs")
    raw_manifest = read_bytes(expected_key + "/manifest.json", 2 * 1024**2)
    if marketdata_bytes(dataset["manifest"]) != raw_manifest:
        raise ValueError("Analysis dataset manifest row differs")
    artifacts = {
        name: read_bytes(expected_key + "/" + name, 32 * 1024**2)
        for name in ("preview.json", "sessions.json")
    }
    source = parse_retained_source(
        dataset_id=UUID(did),
        manifest_sha256=dataset["content_sha256"],
        manifest_bytes=raw_manifest,
        artifacts=artifacts,
        policy=policy,
        input_names=("close",),
    )
    labels = derive_configured_labels(source, objective)
    if (
        source.dataset != config.input.dataset
        or source.universe != config.input.calculation_universe
        or source.calendar.sha256 != config.input.calendar_sha256
        or source.calendar.timezone != config.input.market_timezone
    ):
        raise ValueError("Analysis source calendar/universe differs")
    obj = manifest.assembled_rows
    raw_rows = read_bytes(obj.storage_key, obj.size_bytes)
    parsed = parse_json(
        raw_rows,
        limits=JsonLimits(
            max_bytes=obj.size_bytes, max_nodes=1000000, max_items=100000
        ),
    )
    if (
        digest(parsed) != obj.sha256
        or canonical_bytes(parsed) != raw_rows
        or len(raw_rows) != obj.size_bytes
        or set(parsed) != {"schema_version", "input_sha256", "rows"}
        or parsed["schema_version"] != "mineagent.execution-feature-rows.v1"
        or parsed["input_sha256"] != manifest.input_sha256
    ):
        raise ValueError("Analysis snapshot row bytes differ")
    rows = tuple(FeatureRow.model_validate(row) for row in parsed["rows"])
    projection = bind_completed_snapshot(
        snapshot_id=manifest.snapshot_id,
        snapshot_input=manifest.input,
        output_sha256=manifest.output_rows_sha256,
        rows=rows,
        label_manifest=labels.manifest,
        labels=labels.rows,
        calendar_sessions=tuple(day.session_date for day in labels.calendar.sessions),
    )
    value = DirectInput(
        protocol=projection.protocol,
        factors=projection.factors,
        labels=projection.labels,
        calendar_sessions=projection.calendar_sessions,
    )
    raw = write_input(value)
    import hashlib

    if (
        intent["label_manifest"] != labels.manifest.model_dump(mode="json")
        or intent["label_manifest_sha256"] != labels.manifest.sha256
        or intent["label_recipe_sha256"] != labels.label_recipe_sha256
        or intent["protocol"] != value.protocol.model_dump(mode="json")
        or intent["protocol_sha256"] != value.protocol.sha256
        or intent["analysis_input_sha256"] != hashlib.sha256(raw).hexdigest()
        or intent["analysis_input_size_bytes"] != len(raw)
    ):
        raise ValueError("Analysis complete retained input differs")
    return value, projection, objective
