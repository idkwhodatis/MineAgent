"""Exact retained input reconstruction for025 cold/historical views, never launch."""

import hashlib
from types import SimpleNamespace
from uuid import UUID

from packages.factors.contracts import FeatureRow, canonical_bytes, digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.marketdata.normalize import canonical_json as marketdata_bytes
from packages.rdagent_adapter.configured_source import ARTIFACTS as SOURCE_ARTIFACTS
from packages.rdagent_adapter.configured_source import parse_retained_source
from packages.strategy_sdk import JsonLimits, parse_json
from packages.structured_factor_strategy.assembly import assemble_input
from packages.structured_factor_strategy.contracts import StructuredProgram
from packages.structured_factor_strategy.market import ARTIFACTS, stage_market
from packages.structured_factor_strategy.run import StructuredRunConfig
from packages.structured_factor_strategy.wire import write_input


def reconstruct_input(
    *, run, spec, version, execution, publication, dataset, read_bytes
):
    """Caller independently validates predecessor DB closures before invoking."""
    config = StructuredRunConfig.model_validate_json(spec["config_canonical"])
    program = StructuredProgram.model_validate_json(version["program_canonical"])
    manifest = ExecutionSnapshotManifest.model_validate(publication["manifest"])
    owner = execution["owner_id"]
    eid = execution["id"]
    if (
        canonical_bytes(config).decode("ascii") != spec["config_canonical"]
        or config.model_dump(mode="json") != spec["config"]
        or digest(config) != spec["config_sha256"]
        or canonical_bytes(program).decode("ascii") != version["program_canonical"]
        or program.model_dump(mode="json") != version["program"]
        or digest(program) != version["program_sha256"]
    ):
        raise ValueError("Structured raw declaration differs")
    if (
        run["owner_id"],
        run["id"],
        run["purpose"],
        run["engine"],
        run["generation"],
        run["config"],
        run["request_sha256"],
    ) != (
        owner,
        execution["run_id"],
        "factor_strategy",
        "structured",
        1,
        spec["config"],
        spec["config_sha256"],
    ) or run["strategy_version_id"] is not None:
        raise ValueError("Structured run identity differs")
    if (
        spec["owner_id"],
        spec["run_id"],
        spec["structured_strategy_version_id"],
        version["owner_id"],
        version["id"],
        version["program_sha256"],
    ) != (
        owner,
        run["id"],
        execution["structured_strategy_version_id"],
        owner,
        str(config.structured_strategy_version_id),
        config.program_sha256,
    ):
        raise ValueError("Structured owned strategy binding differs")
    if (
        (
            dataset["owner_id"],
            dataset["id"],
            dataset["content_sha256"],
            run["dataset_version_id"],
        )
        != (owner, str(config.dataset_version_id), config.dataset_sha256, dataset["id"])
        or dataset["storage_key"] != f"datasets/{owner}/{dataset['id']}"
        or dataset["market"] != "US"
    ):
        raise ValueError("Structured owned retained dataset differs")
    if (
        not 0 <= execution["ordinal"] < len(config.snapshots)
        or str(config.snapshots[execution["ordinal"]]) != execution["snapshot_id"]
        or (
            publication["id"],
            publication["owner_id"],
            publication["factor_version_id"],
            publication["manifest_sha256"],
        )
        != (
            execution["snapshot_id"],
            owner,
            execution["factor_version_id"],
            digest(manifest),
        )
    ):
        raise ValueError("Structured completed snapshot differs")
    key = dataset["storage_key"]
    raw_manifest = read_bytes(key + "/manifest.json", 2 * 1024**2)
    if raw_manifest != marketdata_bytes(dataset["manifest"]):
        raise ValueError("Retained dataset manifest differs")
    artifacts = {
        name: read_bytes(key + "/" + name, 32 * 1024**2) for name in sorted(ARTIFACTS)
    }
    if len(raw_manifest) + sum(map(len, artifacts.values())) > 64 * 1024**2:
        raise ValueError("Structured source read bound")
    source = parse_retained_source(
        dataset_id=config.dataset_version_id,
        manifest_sha256=config.dataset_sha256,
        manifest_bytes=raw_manifest,
        artifacts={k: artifacts[k] for k in SOURCE_ARTIFACTS},
        policy=config.source_policy,
        input_names=tuple(x.name for x in manifest.input.definition.input_fields),
    )
    if (str(source.source_start), str(source.source_end)) != (
        dataset["start_date"],
        dataset["end_date"],
    ):
        raise ValueError("Dataset extent differs")
    market = stage_market(
        raw_manifest,
        artifacts,
        dataset_sha256=config.dataset_sha256,
        window=config.execution_window,
    )
    rows_raw = read_bytes(manifest.assembled_rows.storage_key, 16 * 1024**2)
    rows_doc = parse_json(
        rows_raw,
        limits=JsonLimits(max_bytes=16 * 1024**2, max_nodes=1000000, max_items=100000),
    )
    if (
        type(rows_doc) is not dict
        or set(rows_doc) != {"schema_version", "input_sha256", "rows"}
        or rows_doc["schema_version"] != "mineagent.execution-feature-rows.v1"
        or rows_doc["input_sha256"] != manifest.input_sha256
        or canonical_bytes(rows_doc) != rows_raw
        or hashlib.sha256(rows_raw).hexdigest() != manifest.assembled_rows.sha256
        or len(rows_raw) != manifest.assembled_rows.size_bytes
    ):
        raise ValueError("Structured retained factor row commitment differs")
    rows = tuple(FeatureRow.model_validate(x) for x in rows_doc["rows"])
    scope = SimpleNamespace(
        **{k: UUID(execution[k]) for k in ("owner_id", "run_id", "attempt_id")},
        generation=execution["generation"],
    )
    value = assemble_input(
        fence=scope,
        execution_id=UUID(eid),
        config=config,
        program=program,
        snapshot=manifest,
        rows=rows,
        source=source,
        market=market,
    )
    raw = write_input(value)
    if (
        raw.decode("ascii"),
        value.model_dump(mode="json"),
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        hashlib.sha256(market.content).hexdigest(),
        len(market.content),
    ) != (
        execution["strategy_input_canonical"],
        execution["strategy_input"],
        execution["strategy_input_sha256"],
        execution["strategy_input_size_bytes"],
        execution["market_sha256"],
        execution["market_size_bytes"],
    ):
        raise ValueError("Structured input reconstruction differs")
    return value, market.content, config, program
