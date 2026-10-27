"""Provider successor reconstructs real retained input; no runtime/PG claim."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_rdagent_configured_backup import cold  # noqa: F401
from test_rdagent_configured_inputs import case  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app.rdagent_configured_backup import validate_configured_input_closure
from backend.app.rdagent_snapshot_config import (
    parse_snapshot_config,
    provider_config_in_session,
)
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot
from runtimes.rdagent.probe_fixture import research_dict


def provider(raw, owner):
    snapshot = SteerSnapshot(owner_id=UUID(str(owner)))
    return ProviderFactorResearchConfig.model_validate(
        {
            **raw,
            "schema_version": "mineagent.factor-research.v2",
            "model_profile": "configured.rdagent.v1",
            "steer_snapshot": snapshot.model_dump(mode="json"),
            "steer_snapshot_sha256": snapshot.sha256,
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
    )


def test_provider_completed_input_reconstruction_rereads_actual_retained_files(cold):  # noqa: F811
    witnesses, args = cold
    spec = args["tables"]["factor_research_specs"][0]
    config = provider(spec["config"], spec["owner_id"])
    spec["config"] = config.model_dump(mode="json")
    args["tables"]["factor_analysis_plans"][0]["config_sha256"] = config.sha256
    with pytest.raises(ValueError):
        validate_configured_input_closure(witnesses, **args)
    validate_configured_input_closure(witnesses, **args, provider_config=True)


@pytest.mark.parametrize("json_mode", [False, True])
def test_old_config_remains_exact_and_provider_requires_explicit_successor(json_mode):
    owner = uuid4()
    raw = research_dict()
    old = parse_snapshot_config(raw, owner_id=owner, json_mode=json_mode)
    assert old.schema_version == "mineagent.factor-research.v1"
    value = provider(raw, owner).model_dump(mode="json")
    with pytest.raises(ValueError):
        parse_snapshot_config(value, owner_id=owner, json_mode=json_mode)
    actual = parse_snapshot_config(
        value, owner_id=owner, provider_config=True, json_mode=json_mode
    )
    assert actual.schema_version == "mineagent.factor-research.v2"
    with pytest.raises(ValueError, match="owner differs"):
        parse_snapshot_config(
            value, owner_id=uuid4(), provider_config=True, json_mode=json_mode
        )


@pytest.mark.parametrize(
    "revision,expected",
    [
        ("021_execution_snapshots", False),
        ("022_factor_analysis", False),
        ("023_rd_submissions", False),
        ("024_openevolve_initial", False),
        ("025_structured_factor_strategy", False),
        ("026_rd_seeded_submissions", True),
        ("027_unknown", None),
    ],
)
def test_historical_dispatch_is_exact_installed_revision(revision, expected):
    session = SimpleNamespace(
        scalars=lambda query: SimpleNamespace(all=lambda: [revision])
    )
    if expected is None:
        with pytest.raises(ValueError):
            provider_config_in_session(session)
    else:
        assert provider_config_in_session(session) is expected
