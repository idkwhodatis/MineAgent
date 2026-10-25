"""Build installed upstream Config only from fixed owned profile and typed spec."""

import dataclasses
import hashlib

from .contracts import SearchSpec
from .runtime_profile import PROFILE, validate_source_bound
from .source import region


def build_config(spec: SearchSpec, admitted_seed: bytes):
    from openevolve.config import (
        Config,
        DatabaseConfig,
        EvaluatorConfig,
        EvolutionTraceConfig,
        LLMConfig,
        LLMModelConfig,
        PromptConfig,
    )

    from .broker_client import init_client

    if type(spec) is not SearchSpec:
        raise ValueError("Typed SearchSpec required")
    spec = SearchSpec.model_validate_json(spec.model_dump_json())
    if (
        type(spec) is not SearchSpec
        or spec.maximum_mutation_slots > PROFILE.maximum_mutation_slots
        or spec.population_size > PROFILE.maximum_population
        or spec.archive_size > PROFILE.maximum_archive
    ):
        raise ValueError("Spec exceeds disabled initial runtime profile")
    validate_source_bound(admitted_seed)
    region(admitted_seed)
    if (
        hashlib.sha256(admitted_seed).hexdigest()
        != spec.preparation.prepared.source_sha256
    ):
        raise ValueError("Seed source differs from immutable specification")

    def model():
        return LLMModelConfig(
            name=PROFILE.model_profile,
            api_base=None,
            api_key=None,
            provider=None,
            init_client=init_client,
            weight=1.0,
            temperature=0,
            max_tokens=PROFILE.maximum_output_tokens,
            retries=0,
            timeout=65,
            random_seed=spec.execution.rng_seed,
            manual_mode=False,
        )

    return Config(
        max_iterations=spec.maximum_mutation_slots,
        checkpoint_interval=1,
        random_seed=spec.execution.rng_seed,
        language="python",
        file_suffix=".py",
        max_code_length=PROFILE.maximum_source_characters,
        enforce_evolve_blocks=True,
        diff_based_evolution=True,
        max_tasks_per_child=None,
        early_stopping_patience=None,
        llm=LLMConfig(
            api_base=None,
            api_key=None,
            models=[model()],
            evaluator_models=[model()],
            temperature=0,
            max_tokens=PROFILE.maximum_output_tokens,
            timeout=65,
            retries=0,
            retry_delay=0,
            manual_mode=False,
        ),
        prompt=PromptConfig(
            num_top_programs=1,
            num_diverse_programs=1,
            use_template_stochasticity=False,
            programs_as_changes_description=False,
            use_meta_prompting=False,
            include_artifacts=True,
            max_artifact_bytes=PROFILE.maximum_feedback_bytes,
        ),
        database=DatabaseConfig(
            in_memory=True,
            db_path=None,
            log_prompts=False,
            population_size=spec.population_size,
            archive_size=spec.archive_size,
            num_islands=1,
            feature_dimensions=["complexity", "diversity"],
            random_seed=spec.execution.rng_seed,
            artifacts_base_path=None,
            artifact_size_threshold=32768,
            max_snapshot_artifacts=spec.population_size,
            embedding_model=None,
            embedding_api_base=None,
        ),
        evaluator=EvaluatorConfig(
            timeout=PROFILE.evaluator_timeout_seconds,
            max_retries=0,
            cascade_evaluation=False,
            parallel_evaluations=1,
            distributed=False,
            use_llm_feedback=False,
            enable_artifacts=True,
            max_artifact_storage=PROFILE.maximum_feedback_bytes,
            evaluator_args=None,
        ),
        evolution_trace=EvolutionTraceConfig(enabled=False, include_prompts=False),
    )


def config_manifest(config):
    """No callback pickle or secret reaches durable configuration evidence."""
    from .broker_client import init_client

    def encode(value):
        if value is init_client:
            return {
                "installed_factory": "packages.openevolve_adapter.broker_client.init_client"
            }
        if dataclasses.is_dataclass(value):
            return {
                f.name: encode(getattr(value, f.name))
                for f in dataclasses.fields(value)
            }
        if type(value) is dict:
            return {k: encode(v) for k, v in value.items()}
        if type(value) in (list, tuple):
            return [encode(v) for v in value]
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError("Executable or unsupported config value")

    manifest = encode(config)
    for model in [*manifest["llm"]["models"], *manifest["llm"]["evaluator_models"]]:
        if (
            model["api_key"] is not None
            or model["api_base"] is not None
            or model["init_client"]
            != {
                "installed_factory": "packages.openevolve_adapter.broker_client.init_client"
            }
        ):
            raise ValueError("Unapproved model transport config")
    return manifest
