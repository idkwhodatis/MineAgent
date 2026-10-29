"""024 immutable run/source/profile agreement for cold closure; no launch grant."""

import hashlib
from types import SimpleNamespace
from uuid import UUID

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.persisted_identity import require_search_identity
from packages.research_runtime.broker_contracts import digest

RUN_UUIDS = frozenset(
    {
        "id",
        "owner_id",
        "dataset_version_id",
        "strategy_version_id",
        "experiment_id",
        "split_version_id",
        "final_selection_id",
        "active_attempt_id",
        "latest_checkpoint_id",
        "resume_checkpoint_id",
    }
)


def validate_specs(closure, research_closure):
    tables, external = closure["tables"], closure["external_bindings"]
    runs = {r["id"]: r for r in external["runs"]}
    from backend.app.openevolve_backup_partition import V11, V12, validate_partition

    shared = {
        r["id"]: r for r in validate_partition(external["runs"], research_closure)
    }
    specs = {r["run_id"]: r for r in tables["strategy_search_specs"]}
    if (
        len(runs) != len(external["runs"])
        or len(specs) != len(tables["strategy_search_specs"])
        or set(runs) != set(shared)
        or set(specs) != set(runs)
    ):
        raise ValueError("OE run/spec inventory differs")
    strategies = {r["id"]: r for r in external["strategy_versions"]}
    datasets = {r["id"]: r for r in external["dataset_versions"]}
    controls = {
        r["run_id"]: r for r in research_closure["tables"]["research_budget_controls"]
    }
    used_strategies, used_datasets, result = set(), set(), {}
    for identity, row in runs.items():
        projected = shared[identity]
        if research_closure.get("schema_version") in {V11, V12}:
            # This new shared projection is checked against the actual full
            # config above; the frozen024 full-run inventory stays unchanged.
            projected = {
                k: v for k, v in projected.items() if k != "config_schema_version"
            }
        if any(row[k] != v for k, v in projected.items()):
            raise ValueError("OE shared/full run binding differs")
        run = SimpleNamespace(
            **{
                k: UUID(v) if k in RUN_UUIDS and v is not None else v
                for k, v in row.items()
            }
        )
        spec = require_search_identity(run)
        saved = specs[identity]
        expected = {
            "owner_id": str(spec.owner_id),
            "run_id": str(spec.run_id),
            "spec_sha256": spec.sha256,
            "spec": spec.model_dump(mode="json"),
            "original_strategy_id": str(spec.preparation.original.strategy_version_id),
            "original_source_sha256": spec.preparation.original.source_sha256,
            "prepared_strategy_id": str(spec.preparation.prepared.strategy_version_id),
            "prepared_source_sha256": spec.preparation.prepared.source_sha256,
            "dataset_id": str(spec.execution.dataset_version_id),
            "dataset_sha256": spec.execution.dataset_sha256,
            "objective_sha256": digest(spec.objective),
            "execution_sha256": digest(spec.execution),
        }
        if any(canonical(saved[k]) != canonical(v) for k, v in expected.items()):
            raise ValueError("OE frozen specification projection differs")
        budget = controls.get(identity)
        if (
            budget is None
            or budget["owner_id"] != row["owner_id"]
            or canonical(budget["limits"])
            != canonical(spec.budget.model_dump(mode="json"))
        ):
            raise ValueError("OE immutable global budget differs")
        for binding in (spec.preparation.original, spec.preparation.prepared):
            key = str(binding.strategy_version_id)
            source = strategies.get(key)
            if (
                source is None
                or source["owner_id"] != row["owner_id"]
                or source["content_sha256"] != binding.source_sha256
                or type(source["code"]) is not str
                or hashlib.sha256(source["code"].encode()).hexdigest()
                != binding.source_sha256
                or digest(source["manifest"]) != binding.manifest_sha256
            ):
                raise ValueError("OE immutable strategy bytes differ")
            used_strategies.add(key)
        key = str(spec.execution.dataset_version_id)
        dataset = datasets.get(key)
        if (
            dataset is None
            or dataset["owner_id"] != row["owner_id"]
            or dataset["content_sha256"] != spec.execution.dataset_sha256
        ):
            raise ValueError("OE immutable dataset differs")
        used_datasets.add(key)
        result[identity] = spec
    if set(strategies) != used_strategies or set(datasets) != used_datasets:
        raise ValueError("OE external input inventory differs")
    return result
