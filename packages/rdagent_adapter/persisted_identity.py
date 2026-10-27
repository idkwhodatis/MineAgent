"""Closed persisted RD source identity, not submission or execution permission."""

import json

from packages.rdagent_adapter.provider_config import parse_research_config

LEGACY_ONLY_FIELDS = (
    "strategy_version_id",
    "experiment_id",
    "split_version_id",
    "final_selection_id",
    "submission_binding",
    "portable_policy_image_digest",
    "latest_checkpoint_id",
    "resume_checkpoint_id",
)


def require_source_identity(run):
    if (run.purpose, run.engine) != ("factor_research", "rdagent"):
        raise ValueError("Exact RD persisted identity required")
    if type(run.config) is not dict or any(
        getattr(run, field) is not None for field in LEGACY_ONLY_FIELDS
    ):
        raise ValueError("Legacy fields cannot stand in for RD identity")
    config = parse_research_config(json.loads(json.dumps(run.config, allow_nan=False)))
    if (
        hasattr(config, "steer_snapshot")
        and config.steer_snapshot.owner_id != run.owner_id
    ):
        raise ValueError("Frozen steer owner differs from research owner")
    if (
        config.model_dump(mode="json") != run.config
        or run.dataset_version_id != config.input.dataset.dataset_version_id
        or run.request_sha256 != config.sha256
        or run.research_image_digest not in (None, config.runtime.orchestrator_image)
    ):
        raise ValueError("RD immutable identity differs from exact canonical config")
    return config
