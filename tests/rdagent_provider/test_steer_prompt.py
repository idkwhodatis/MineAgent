from hashlib import sha256
from uuid import UUID

import pytest

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.provider_prompt import first_provider_messages
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion
from runtimes.rdagent.probe_fixture import research_dict


def configuration(text="Research normalized high-low range, not momentum"):
    version = SteerVersion(
        steer_id=UUID(int=1),
        version_id=UUID(int=2),
        ordinal=1,
        kind="hypothesis",
        text=text,
        text_sha256=sha256(text.encode()).hexdigest(),
    )
    snapshot = SteerSnapshot(owner_id=UUID(int=3), versions=(version,))
    raw = research_dict()
    raw.update(
        schema_version="mineagent.factor-research.v2",
        max_iterations=1,
        model_profile="configured.rdagent.v1",
        steer_snapshot=snapshot.model_dump(mode="json"),
        steer_snapshot_sha256=snapshot.sha256,
        model_binding={
            "profile_id": "offline",
            "profile_sha256": "a" * 64,
            "chat_model": "chat",
            "embedding_model": "embed",
            "embedding_dimensions": 2,
            "max_output_tokens": 100,
            "request_deadline_ms": 8000,
            "pricing": {"status": "unavailable"},
        },
    )
    raw["task"] = {
        **raw["task"],
        "name": "range",
        "description": text,
        "formulation": "(high-low)/close",
        "output_unit": "ratio",
    }
    return ProviderFactorResearchConfig.model_validate(raw)


def test_prompt_contains_actual_frozen_version_and_nonmomentum_task():
    config = configuration()
    system, user = first_provider_messages(config)
    assert canonical_bytes(config.steer_snapshot).decode() in system
    assert str(config.steer_snapshot.versions[0].version_id) in system
    assert "(high-low)/close" in user
    assert "compliance" in system


def test_later_steer_version_does_not_change_frozen_prompt():
    config = configuration()
    old = first_provider_messages(config)
    changed = configuration("Investigate volume dispersion instead")
    assert first_provider_messages(changed) != old
    assert first_provider_messages(config) == old
    assert config.steer_snapshot_sha256 != changed.steer_snapshot_sha256


def test_snapshot_hash_drift_rejected():
    raw = configuration().model_dump(mode="json")
    raw["steer_snapshot_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="snapshot hash"):
        ProviderFactorResearchConfig.model_validate(raw)
