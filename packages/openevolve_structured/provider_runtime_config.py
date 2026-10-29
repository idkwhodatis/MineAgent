"""Explicit normal provider Config; old fixture factory/config bytes unchanged."""

import dataclasses

from .candidate import _raw_fields
from .provider_contracts import OEChatBinding
from .provider_prompt import prompt_config


def build_config(spec, seed, binding):
    from .provider_broker_client import init_client
    from .runtime_config import build_config as build_fixture_config

    if type(binding) is not OEChatBinding:
        raise ValueError("Exact public operator model binding required")
    binding = OEChatBinding.model_validate(_raw_fields(binding))
    # Reuse unchanged bounded initial-epoch algorithms, but explicitly replace
    # both factories and model limits before the Config can enter upstream.
    config = build_fixture_config(spec, seed)
    models = [
        dataclasses.replace(
            model,
            name=binding.model,
            max_tokens=binding.max_output_tokens,
            init_client=init_client,
        )
        for model in config.llm.models
    ]
    evaluator_models = [
        dataclasses.replace(
            model,
            name=binding.model,
            max_tokens=binding.max_output_tokens,
            init_client=init_client,
        )
        for model in config.llm.evaluator_models
    ]
    config = dataclasses.replace(
        config,
        llm=dataclasses.replace(
            config.llm,
            models=models,
            evaluator_models=evaluator_models,
            max_tokens=binding.max_output_tokens,
        ),
        prompt=prompt_config(),
    )
    config_manifest(config)
    return config


def config_manifest(config):
    from .provider_broker_client import init_client

    factory = {
        "installed_factory": "packages.openevolve_structured.provider_broker_client.init_client"
    }

    def encode(value):
        if value is init_client:
            return factory
        if dataclasses.is_dataclass(value):
            return {
                field.name: encode(getattr(value, field.name))
                for field in dataclasses.fields(value)
            }
        if type(value) is dict:
            return {key: encode(item) for key, item in value.items()}
        if type(value) in (list, tuple):
            return [encode(item) for item in value]
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError("Unapproved executable provider Config value")

    result = encode(config)
    for model in [*result["llm"]["models"], *result["llm"]["evaluator_models"]]:
        if (
            model["api_key"] is not None
            or model["api_base"] is not None
            or model["provider"] is not None
            or model["init_client"] != factory
            or model["retries"] != 0
            or model["temperature"] != 0
            or not 1 <= model["max_tokens"] <= 4096
        ):
            raise ValueError("Normal provider Config cannot access an endpoint")
    return result
