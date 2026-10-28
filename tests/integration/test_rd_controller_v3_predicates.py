"""Real migrated027 helpers; not an SDK/kernel or full cold-restore proof."""

import copy
import json
from uuid import UUID

import pytest
from sqlalchemy import text

from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot
from runtimes.rdagent.probe_fixture import research_dict

pytestmark = pytest.mark.postgres
V2 = "mineagent.rdagent-orchestrator.v2"
V3 = "mineagent.rdagent-orchestrator.v3"


def configured(provider=False):
    raw = research_dict()
    raw["max_iterations"] = 1
    raw["budget"]["wall_milliseconds"] = 600000
    if not provider:
        return FactorResearchConfig.model_validate(raw).model_dump(mode="json")
    steer = SteerSnapshot(owner_id=UUID(int=123))
    return ProviderFactorResearchConfig.model_validate(
        {
            **raw,
            "schema_version": "mineagent.factor-research.v2",
            "model_profile": "configured.rdagent.v1",
            "steer_snapshot": steer.model_dump(mode="json"),
            "steer_snapshot_sha256": steer.sha256,
            "model_binding": {
                "profile_id": "offline",
                "profile_sha256": "a" * 64,
                "chat_model": "chat",
                "embedding_model": "embed",
                "embedding_dimensions": 2,
                "max_output_tokens": 100,
                "request_deadline_ms": 8000,
                "pricing": {"status": "unavailable"},
            },
        }
    ).model_dump(mode="json")


@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("selection", ["missing", V3, None, False, 120, V2, "unknown"])
def test_exact_controller_config_selection(migrated_engine, provider, selection):
    value = configured(provider)
    if selection != "missing":
        value["controller_profile"] = selection
    with migrated_engine.connect() as c:
        actual = c.scalar(
            text("SELECT mineagent_rd_source_config(CAST(:v AS jsonb))"),
            {"v": json.dumps(value)},
        )
    assert actual is (selection in ("missing", V3))


@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_explicit_composed_total_wall_floor(migrated_engine, provider, offset):
    value = configured(provider)
    value["controller_profile"] = V3
    value["budget"]["wall_milliseconds"] = (556000 if provider else 300000) + offset
    with migrated_engine.connect() as c:
        actual = c.scalar(
            text("SELECT mineagent_rd_source_config(CAST(:v AS jsonb))"),
            {"v": json.dumps(value)},
        )
    assert actual is (offset >= 0)
    cls = ProviderFactorResearchConfig if provider else FactorResearchConfig
    if offset < 0:
        with pytest.raises(ValueError):
            cls.model_validate(value)
    else:
        assert cls.model_validate(value).controller_profile == V3


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("profile", [V2, V3, None, "unknown"])
def test_profile_selection_is_not_inferred_from_larger_budget(
    migrated_engine, selected, profile
):
    value = configured()
    if selected:
        value["controller_profile"] = V3
    with migrated_engine.connect() as c:
        actual = c.scalar(
            text(
                "SELECT mineagent_rd_controller_profile_matches(CAST(:v AS jsonb),:p)"
            ),
            {"v": json.dumps(value), "p": profile},
        )
    assert actual is (profile == (V3 if selected else V2))


def test_frozen026_helper_does_not_start_accepting_new_config(migrated_engine):
    old = configured(True)
    new = copy.deepcopy(old)
    new["controller_profile"] = V3
    with migrated_engine.connect() as c:
        for value, expected in [(old, True), (new, False)]:
            assert (
                c.scalar(
                    text(
                        "SELECT mineagent_rd_provider_config_026_frozen(CAST(:v AS jsonb))"
                    ),
                    {"v": json.dumps(value)},
                )
                is expected
            )
