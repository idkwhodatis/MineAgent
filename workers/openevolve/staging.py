"""Native dataset mechanics for search; no legacy RunConfig/admission shortcut."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from backend.app.storage import read_verified, safe_path
from packages.openevolve_adapter.contracts import (
    DevelopmentWindow,
    StrategyExecutionPlan,
)
from packages.openevolve_adapter.execution_inputs import (
    ResolvedNativeInputs,
    seeded_rng_state,
)
from packages.openevolve_adapter.runtime_profile import validate_source_bound
from packages.research_runtime.broker_contracts import digest
from packages.tradegrad_adapter.config import DateWindow, VersionRef, canonical_bytes
from packages.tradegrad_adapter.windows import (
    DEFAULT_LIMITS,
    DatasetWindowReport,
    _stage_resolved_dataset_window,
)


@dataclass(frozen=True)
class DatasetMechanics:
    dataset: VersionRef
    universe: VersionRef
    task: str
    warmup: object
    strategy_contract: object


def calendar_projection(manifest):
    return {
        "listings": [
            {key: row.get(key) for key in ("listing_id", "calendar", "calendar_note")}
            for row in manifest.get("listings", [])
        ],
        "coverage": manifest.get("coverage", []),
    }


def render_search_input(
    *,
    plan: StrategyExecutionPlan,
    inputs: ResolvedNativeInputs,
    window: DevelopmentWindow,
    manifest,
    storage_root,
    dataset_storage_key,
    source: bytes,
    source_sha256: str,
    max_bytes,
):
    import hashlib

    # Reparse copied instances and enforce exact member window before reading
    # data; no caller-selected final/holdout dates can enter this path.
    plan = StrategyExecutionPlan.model_validate_json(plan.model_dump_json())
    inputs = ResolvedNativeInputs.model_validate_json(inputs.model_dump_json())
    inputs.validate_plan(plan)
    if window not in plan.windows:
        raise ValueError("Window is not in frozen development execution plan")
    validate_source_bound(source)
    if hashlib.sha256(source).hexdigest() != source_sha256:
        raise ValueError("Candidate source bytes differ")
    expected = {
        "listing_map_sha256": digest(manifest.get("evaluator_symbol_mapping")),
        "calendar_sha256": digest(calendar_projection(manifest)),
        "price_semantics_sha256": digest(manifest.get("price_semantics")),
    }
    if "price_semantics" not in manifest or any(
        getattr(plan, k) != v for k, v in expected.items()
    ):
        raise ValueError("Resolved dataset semantics differ from plan")
    if hashlib.sha256(canonical_bytes(manifest)).hexdigest() != plan.dataset_sha256:
        raise ValueError("Pinned dataset manifest differs")
    if (
        not 1 <= len(manifest.get("files", {})) <= 32
        or sum(f.size_bytes for f in plan.files) > 64 * 1024 * 1024
    ):
        raise ValueError("Dataset exceeds fixed staging inventory bound")
    contents = {
        name: read_verified(
            storage_root, f"{dataset_storage_key}/{name}", sha, max_bytes
        )
        for name, sha in manifest.get("files", {}).items()
    }
    inventory = Counter(
        (hashlib.sha256(raw).hexdigest(), len(raw)) for raw in contents.values()
    )
    if inventory != Counter((f.sha256, f.size_bytes) for f in plan.files):
        raise ValueError("Exact dataset byte inventory differs from plan")
    mechanics = DatasetMechanics(
        VersionRef(id=str(plan.dataset_version_id), sha256=plan.dataset_sha256),
        VersionRef(id=inputs.universe_id, sha256=plan.universe_sha256),
        inputs.task,
        inputs.warmup,
        inputs.strategy_contract,
    )
    staged = _stage_resolved_dataset_window(
        mechanics,
        "validation",
        DateWindow(start=window.start, end=window.end),
        manifest,
        contents.__getitem__,
        limits=DEFAULT_LIMITS,
    )
    request = {
        "task": inputs.task,
        "market": plan.market,
        "profile": "corrected_explicit_config",
        "start_date": str(window.start),
        "end_date": str(window.end),
        "evaluation_config": {
            "engine_overrides": inputs.market_profile.engine_kwargs(),
            "window": {
                "start": str(window.start),
                "end": str(window.end),
                "role": "validation",
            },
            "warmup": {
                "sessions": inputs.warmup.sessions,
                "history_policy": inputs.warmup.history_policy,
            },
            "benchmark": None,
            "run_config_sha256": digest(plan),
            "result_policy": "independent_observation.v1",
            "research_capture": inputs.capture.materialize(),
            "strategy_contract": inputs.strategy_contract.model_dump(mode="json"),
        },
        "runtime_observation": {
            "schema_version": "tradegrad.runtime-observation-request.v1",
            "rng_restore": seeded_rng_state(plan.rng_seed),
        },
    }
    files = {
        **staged.files,
        "strategy.py": source,
        "request.json": canonical_bytes(request),
        "stage-report.json": canonical_bytes(
            TypeAdapter(DatasetWindowReport).dump_python(staged.report, mode="json")
        ),
    }
    if len(files) > 256 or sum(map(len, files.values())) > 66 * 1024**2:
        raise ValueError("Rendered native staging exceeds fixed profile")
    return staged.report, files


def stage_search_input(*, target, **kwargs):
    """Known staging utility; production admission must precede this call."""
    report, files = render_search_input(**kwargs)
    target = Path(target)
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("Input destination cannot traverse symlinks")
    target.mkdir(parents=True, mode=0o700)
    for name, content in files.items():
        path = safe_path(target, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    for path in target.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    target.chmod(0o555)
    return report
