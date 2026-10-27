"""Data-only022 calendar reconstruction; final analysis closure remains required."""

from uuid import UUID

from backend.app.factor_analysis_backup import indexes
from backend.app.rdagent_complete_evaluation import maintained_calendar
from packages.factors.contracts import digest
from packages.marketdata.normalize import canonical_json
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)


def cold_calendar(config, publication, analysis_closure, read_json):
    idx = indexes(analysis_closure, 100000)
    plans = [
        p
        for p in idx["factor_analysis_plans"].values()
        if (p["owner_id"], p["run_id"])
        == (publication["owner_id"], publication["run_id"])
    ]
    if not plans:
        return maintained_calendar(config)
    if len(plans) != 1:
        raise ValueError("Configured calendar plan duplicates")
    plan = plans[0]
    dataset = idx["dataset_versions"][plan["label_source_dataset_id"]]
    policy = ConfiguredSourcePolicy.model_validate(plan["source_policy"])
    expected = f"datasets/{plan['owner_id']}/{dataset['id']}"
    if (
        plan["config_sha256"] != config.sha256
        or plan["input_sha256"] != config.input.sha256
        or plan["source_policy_sha256"] != digest(policy)
        or dataset["storage_key"] != expected
        or dataset["owner_id"] != plan["owner_id"]
        or dataset["content_sha256"] != plan["label_source_manifest_sha256"]
    ):
        raise ValueError("Configured calendar plan/source differs")
    source = parse_retained_source(
        dataset_id=UUID(dataset["id"]),
        manifest_sha256=dataset["content_sha256"],
        manifest_bytes=canonical_json(read_json(expected + "/manifest.json")),
        artifacts={
            n: canonical_json(read_json(expected + "/" + n))
            for n in ("preview.json", "sessions.json")
        },
        policy=policy,
        input_names=tuple(f.name for f in config.input.input_fields),
    )
    if (
        source.dataset != config.input.dataset
        or source.universe != config.input.calculation_universe
        or source.input_fields != config.input.input_fields
        or source.calendar.sha256 != config.input.calendar_sha256
        or source.calendar.timezone != config.input.market_timezone
    ):
        raise ValueError("Configured retained calendar differs")
    return source.calendar
