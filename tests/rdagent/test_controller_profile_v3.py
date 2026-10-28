"""Closed profile boundaries; no container or provider acceptance claimed."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_rdagent_contracts import research_dict
from test_rdagent_controller_process import records

from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.controller_profile import (
    COMPOSED_CONTROLLER_PROFILE as NEW,
)
from packages.rdagent_adapter.controller_profile import (
    LEGACY_CONTROLLER_PROFILE as OLD,
)
from packages.rdagent_adapter.controller_profile import (
    composed_wall_floor,
    controller_profile,
    controller_selection_fields,
    controller_wall_milliseconds,
)
from runtimes.rdagent_operational.deadline import remaining_seconds
from runtimes.rdagent_operational.process_journal import ControllerIntent
from runtimes.rdagent_operational.supervisor import controller_command


def test_missing_profile_preserves_exact_old_canonical_and_revalidation():
    old = json.loads(
        (Path(__file__).parent / "fixtures/controller-profile-legacy.json").read_text()
    )
    config = FactorResearchConfig.model_validate(research_dict())
    assert canonical_bytes(config).decode() == old["canonical"]
    assert config.sha256 == old["sha256"]
    assert canonical_bytes(
        FactorResearchConfig.model_validate(config)
    ) == canonical_bytes(config)
    assert controller_profile(config) == OLD
    assert controller_selection_fields(config) == {}


@pytest.mark.parametrize("value", [None, OLD, "v3", "", 120, True, {}])
def test_only_explicit_exact_new_profile_is_accepted(value):
    with pytest.raises(ValueError):
        FactorResearchConfig.model_validate(
            {**research_dict(), "controller_profile": value}
        )


def test_new_profile_is_retained_hashed_and_never_inferred_from_large_budget():
    legacy = FactorResearchConfig.model_validate(research_dict())
    new = FactorResearchConfig.model_validate(
        {**research_dict(), "max_iterations": 1, "controller_profile": NEW}
    )
    assert new.budget == legacy.budget
    assert new.sha256 != legacy.sha256
    assert new.model_dump(mode="json")["controller_profile"] == NEW
    assert controller_profile(new) == NEW
    assert controller_profile(legacy) == OLD
    assert controller_selection_fields(new) == {"controller_profile": NEW}
    assert FactorResearchConfig.model_validate(new).sha256 == new.sha256
    for invalid_value in ("unknown", None):
        invalid = new.model_copy(update={"controller_profile": invalid_value})
        with pytest.raises(ValueError):
            FactorResearchConfig.model_validate(invalid)


@pytest.mark.parametrize("profile,limit", [(OLD, 60000), (NEW, 120000)])
def test_absolute_pid1_limit_never_renews_or_accepts_excess(profile, limit):
    assert controller_wall_milliseconds(profile) == limit
    assert composed_wall_floor(profile) == limit + 180000
    assert remaining_seconds(str(1000 + limit), 1000, profile=profile) == limit / 1000
    assert (
        remaining_seconds(str(1000 + limit), 2000, profile=profile) == limit / 1000 - 1
    )
    for end in (1000, 999, 1001 + limit, 181000):
        with pytest.raises(ValueError):
            remaining_seconds(str(end), 1000, profile=profile)


@pytest.mark.parametrize("profile,seconds", [(OLD, 60), (NEW, 120)])
def test_intent_and_exact_container_command_bind_profile(tmp_path, profile, seconds):
    intent = records(tmp_path)[0].intent
    document = intent.model_dump(mode="python")
    document["admission"]["profile"] = profile
    document["not_after"] = document["issued_at"] + timedelta(seconds=seconds)
    accepted = ControllerIntent.model_validate(document)
    expected = [str(int(accepted.not_after.timestamp() * 1000))]
    if profile == NEW:
        expected.append(NEW)
    assert controller_command(accepted) == expected
    document["not_after"] += timedelta(microseconds=1)
    with pytest.raises(ValueError):
        ControllerIntent.model_validate(document)


@pytest.mark.parametrize(
    "revision",
    [
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "028_unknown",
    ],
)
@pytest.mark.parametrize("selected", [False, True])
def test_configured_writer_revision_and_profile_are_both_required(revision, selected):
    from types import SimpleNamespace

    from packages.research_runtime.process_contracts import ProcessError
    from workers.research.rdagent_configured import require_controller_revision

    raw = {**research_dict(), "max_iterations": 1}
    if selected:
        raw["controller_profile"] = NEW
    config = FactorResearchConfig.model_validate(raw)
    session = SimpleNamespace(
        scalars=lambda sql: SimpleNamespace(all=lambda: [revision])
    )
    if revision == "028_unknown" or (selected and revision != "027_rd_controller_v3"):
        with pytest.raises(ProcessError):
            require_controller_revision(session, config)
    else:
        require_controller_revision(session, config)
