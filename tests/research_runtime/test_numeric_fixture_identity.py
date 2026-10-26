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
