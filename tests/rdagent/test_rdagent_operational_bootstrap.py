from uuid import UUID

import pytest

from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from runtimes.rdagent.probe_fixture import research_dict


def bootstrap():
    config = FactorResearchConfig.model_validate(research_dict())
    return RDOperationalBootstrap(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        spec_id=UUID(int=4),
        attempt_binding_id=UUID(int=5),
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )


def test_bootstrap_exact_json_hash_and_no_reusable_secret():
    value = bootstrap()
    assert RDOperationalBootstrap.model_validate_json(value.model_dump_json()) == value
    assert not {"lease_token", "ticket", "provider_key", "argv", "environment"} & set(
        value.model_dump()
    )
    changed = value.model_dump(mode="json")
    changed["attempt_id"] = str(UUID(int=6))
    assert RDOperationalBootstrap.model_validate(changed).sha256 != value.sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("config_sha256", "0" * 64),
        ("input_sha256", "0" * 64),
        ("continuation", "resume"),
        ("argv", ["sh"]),
        ("input_layout", "extra_source_mount"),
        ("input_mounts", ["/input/bootstrap.json", "/input/data.json"]),
    ],
)
def test_bootstrap_rejects_coercion_drift_and_unregistered_resume(field, value):
    raw = bootstrap().model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValueError):
        RDOperationalBootstrap.model_validate(raw)
