"""Exact revision024 persisted search identity, not public submission authority."""

from uuid import UUID

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchSpec


def require_search_identity(run):
    if (
        (run.purpose, run.engine) != ("strategy_search", "openevolve")
        or type(run.owner_id) is not UUID
        or type(run.id) is not UUID
        or type(run.config) is not dict
    ):
        raise ValueError("exact search identity")
    spec = SearchSpec.model_validate_json(canonical(run.config))
    if (
        spec.model_dump(mode="json") != run.config
        or (spec.owner_id, spec.run_id) != (run.owner_id, run.id)
        or run.request_sha256 != spec.sha256
        or run.strategy_version_id != spec.preparation.prepared.strategy_version_id
        or run.dataset_version_id != spec.execution.dataset_version_id
        or run.research_image_digest not in (None, spec.runtime_image)
    ):
        raise ValueError("immutable persisted search scope")
    if any(
        getattr(run, key, None) is not None
        for key in (
            "experiment_id",
            "split_version_id",
            "final_selection_id",
            "submission_binding",
            "portable_policy_image_digest",
            "latest_checkpoint_id",
            "resume_checkpoint_id",
        )
    ):
        raise ValueError("legacy identities/resume not valid for search")
    if (
        spec.preparation.kind != "none"
        or spec.factors.kind != "none"
        or not 1 <= spec.maximum_mutation_slots <= 2
        or spec.population_size > 8
        or spec.archive_size > 8
    ):
        raise ValueError("initial OE capability only")
    return spec
