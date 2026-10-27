"""Exact physical observation binding; tickets are data, not launch authority.

Only a host service holding the current fenced transaction may resolve one from
persisted evaluation/admission/stop/object rows. An upstream caller cannot supply
its own expected dictionary to authorize a result.
"""

from dataclasses import dataclass
from uuid import UUID

from packages.research_runtime.broker_contracts import digest
from packages.tradegrad_adapter.independent_results import validate_result_bundle
from packages.tradegrad_adapter.results import canonical_json_bytes, sha256_bytes

from .contracts import DevelopmentWindow, SearchSpec
from .objective import native_observation


@dataclass(frozen=True)
class NativeEvaluationBinding:
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int
    evaluation_id: UUID
    candidate_id: UUID | None
    strategy_version_id: UUID | None
    phase: str
    source_sha256: str
    input_manifest_sha256: str
    execution_plan_sha256: str
    evaluator_image: str
    bundle_sha256: str
    window: DevelopmentWindow


def bound_native_observation(
    bundle, binding: NativeEvaluationBinding, spec: SearchSpec
):
    if type(spec) is not SearchSpec:
        raise ValueError("Typed immutable search specification required")
    if type(binding) is not NativeEvaluationBinding:
        raise ValueError("Explicit evaluation binding required")
    plan = spec.execution
    if (binding.owner_id, binding.run_id) != (spec.owner_id, spec.run_id):
        raise ValueError("Evaluation belongs to a different search")
    if type(binding.generation) is not int or binding.generation <= 0:
        raise ValueError("Invalid fenced generation")
    if any(
        type(getattr(binding, name)) is not UUID
        for name in (
            "owner_id",
            "run_id",
            "attempt_id",
            "evaluation_id",
        )
    ):
        raise ValueError("Invalid evaluation identity")
    if binding.phase in ("seed_original_parity", "seed_prepared_parity"):
        source = (
            spec.preparation.original
            if binding.phase == "seed_original_parity"
            else spec.preparation.prepared
        )
        if (
            binding.candidate_id is not None
            or binding.strategy_version_id != source.strategy_version_id
            or binding.source_sha256 != source.source_sha256
        ):
            raise ValueError("Preparation role or exact source substitution")
    elif binding.phase in ("seed", "mutation"):
        if (
            type(binding.candidate_id) is not UUID
            or binding.strategy_version_id is not None
        ):
            raise ValueError("Search evaluation requires an exact candidate")
    else:
        raise ValueError("Unknown search evaluation phase")
    if (
        binding.execution_plan_sha256 != digest(plan)
        or binding.evaluator_image != plan.evaluator_image
        or binding.window not in plan.windows
    ):
        raise ValueError("Execution plan, image or window substitution")
    validate_result_bundle(bundle)
    if sha256_bytes(canonical_json_bytes(bundle)) != binding.bundle_sha256:
        raise ValueError("Registered result bytes differ")
    identity = bundle["identity"]
    required = {
        "strategy_sha256": binding.source_sha256,
        "input_manifest_sha256": binding.input_manifest_sha256,
        "run_config_sha256": binding.execution_plan_sha256,
    }
    if any(identity.get(key) != value for key, value in required.items()):
        raise ValueError("Native result identity substitution")
    window = bundle["window"]
    if (
        window["start"] != binding.window.start.isoformat()
        or window["end"] != binding.window.end.isoformat()
        or window.get("interval") != "half_open"
        or window.get("warmup", {}).get("sessions") != plan.warmup_sessions
    ):
        raise ValueError("Native result window/warmup substitution")
    return native_observation(
        bundle,
        window_id=binding.window.id,
        evaluation_id=binding.evaluation_id,
        expected_bundle_sha256=binding.bundle_sha256,
    )
