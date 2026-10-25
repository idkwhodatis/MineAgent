"""No unknown identity or missing schema is routed to legacy seed execution."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.research_runtime.dispatch import require_seed_identity
from packages.research_runtime.identity import (
    LEGACY_SCHEMAS,
    RunIdentityError,
    classify_legacy_config,
    require_legacy_identity,
)
from tests.research_runtime.fixtures import seed_config


def run(config, **changes):
    values = {"purpose": "legacy_strategy", "engine": "legacy", "config": config}
    values.update(changes)
    return SimpleNamespace(**values)


def test_exact_seed_identity_preserves_input_bytes_and_uses_no_engine_imports():
    config = seed_config()
    original = deepcopy(config)
    assert classify_legacy_config(config) is None
    assert require_legacy_identity(run(config)) is None
    require_seed_identity(run(config))
    assert config == original


@pytest.mark.parametrize("schema", sorted(LEGACY_SCHEMAS))
def test_seven_versioned_families_keep_exact_schema_and_cannot_stage_as_seed(schema):
    config = {"schema_version": schema}
    assert classify_legacy_config(config) == schema
    assert require_legacy_identity(run(config)) == schema
    with pytest.raises(RunIdentityError):
        require_seed_identity(run(config))


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {},
        {"schema_version": None},
        {"schema_version": 1},
        {"schema_version": []},
        {"schema_version": "mineagent.factor-research.v1"},
    ],
)
def test_unsupported_config_is_not_seed(config):
    with pytest.raises(RunIdentityError):
        require_legacy_identity(run(config))


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": "legacy"},
        {"execution_mode": "optimization"},
        {"task": "future"},
        {"market": "US"},
        {"dataset_sha256": "A" * 64},
        {"strategy_sha256": 0},
        {"is_synthetic": 1},
        {"start_date": "2024-02-30"},
        {"end_date": "2023-01-01"},
        {"start_date": "20240101"},
        {"dataset_version_id": "not-uuid"},
        {"strategy_version_id": None},
        {"extra": "unknown"},
        {"schema_version": None},
    ],
)
def test_persisted_seed_contract_is_strict(changes):
    with pytest.raises(RunIdentityError):
        classify_legacy_config(seed_config(**changes))


@pytest.mark.parametrize("key", sorted(seed_config()))
def test_seed_requires_each_enriched_persisted_field(key):
    config = seed_config()
    del config[key]
    with pytest.raises(RunIdentityError):
        classify_legacy_config(config)


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": None},
        {"engine": None},
        {"purpose": "factor_research"},
        {"engine": "rdagent"},
    ],
)
def test_identity_pair_is_explicit_and_closed(changes):
    with pytest.raises(RunIdentityError):
        require_legacy_identity(run(seed_config(), **changes))


def test_missing_identity_attributes_never_get_defaults():
    with pytest.raises(RunIdentityError):
        require_legacy_identity(SimpleNamespace(config=seed_config()))


@pytest.mark.parametrize(
    "module,function",
    [
        ("research_services", "research_config"),
        ("portable_services", "portable_config"),
        ("final_evaluation_services", "final_config"),
        ("portable_final_services", "final_config"),
        ("independent_services", "independent_config"),
        ("comparison_services", "comparison_config"),
    ],
)
def test_existing_codecs_refuse_nonlegacy_identity(module, function):
    import importlib

    from backend.app.services import DomainError

    target = importlib.import_module("backend.app." + module)
    # Each boundary rejects the pair before it attempts to interpret payload.
    with pytest.raises(DomainError):
        getattr(target, function)(run({}, purpose="factor_research", engine="rdagent"))
