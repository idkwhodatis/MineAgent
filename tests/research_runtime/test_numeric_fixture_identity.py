"""Maintained provider input identity only; not actual process acceptance."""

import json
from uuid import uuid4

import pytest

from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.research_runtime.process_contracts import ProcessError
from runtimes.factor_execution.acceptance import frozen_inputs
from runtimes.rdagent.probe_fixture import research_dict
from workers.research.numeric_fixture import maintained_projection


def project(raw):
    config = FactorResearchConfig.model_validate(raw)
    _, expected, _ = frozen_inputs(
        config.runtime.candidate_image, config.runtime.normalizer_image
    )
    return maintained_projection(
        config, factor_version_id=uuid4(), source_utf8=expected["/input/source.py"]
    )


def test_exact_maintained_dataset_and_single_cutoff_keep_two_prior_sessions():
    full, _ = project(research_dict())
    raw = research_dict()
    raw["input"]["window"]["start"] = "2024-03-15"
    one, files = project(raw)
    assert one.output_session == full.output_session
    assert one.provided_sessions == full.provided_sessions
    assert len(one.provided_sessions) == 3
    assert len(json.loads(files["/input/listings.json"])) == 3


@pytest.mark.parametrize(
    "field",
    [
        "content_sha256",
        "input_files_sha256",
        "availability_revision_sha256",
        "calendar_sha256",
    ],
)
def test_unrelated_dataset_or_fixture_input_digest_cannot_launch(field):
    raw = research_dict()
    target = raw["input"] if field == "calendar_sha256" else raw["input"]["dataset"]
    target[field] = "f" * 64
    with pytest.raises(ProcessError, match="fixture_dataset_identity"):
        project(raw)


def _persisted_assertion():
    # Load the exact assertion used by the Docker/PG gate without executing it.
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "integration/test_numeric_persisted_runtime.py"
    spec = importlib.util.spec_from_file_location("numeric_persisted_oracle_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._assert_persisted_oracle_inputs


def _bound_oracle():
    raw = research_dict()
    raw["max_output_rows_per_cutoff"] = 10000
    raw["max_output_bytes_per_cutoff"] = 16777216
    raw["input"]["dataset"]["dataset_version_id"] = str(uuid4())
    config = FactorResearchConfig.model_validate(raw)
    version_id = uuid4()
    _, expected, _ = frozen_inputs(
        config.runtime.candidate_image, config.runtime.normalizer_image
    )
    _, actual = maintained_projection(
        config, factor_version_id=version_id, source_utf8=expected["/input/source.py"]
    )
    return config, version_id, actual, expected


def test_persisted_oracle_binds_exact_config_without_changing_primitive_defaults():
    config, version, actual, expected = _bound_oracle()
    before = dict(expected)
    primitive = json.loads(expected["/input/manifest.json"])
    assert (primitive["max_output_rows"], primitive["max_output_bytes"]) == (3, 10000)
    _persisted_assertion()(config, version, actual, expected)
    assert expected == before
    assert (
        json.loads(actual["/input/manifest.json"])["snapshot_input_sha256"]
        != primitive["snapshot_input_sha256"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_output_rows", 3),
        ("max_output_bytes", 10000),
        ("snapshot_input_sha256", "f" * 64),
        ("observation_count", 8),
        ("unexpected", "not ignored"),
    ],
)
def test_persisted_oracle_rejects_manifest_drift(field, value):
    config, version, actual, expected = _bound_oracle()
    manifest = json.loads(actual["/input/manifest.json"])
    manifest[field] = value
    actual["/input/manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(AssertionError):
        _persisted_assertion()(config, version, actual, expected)
