"""Explicit same-route family grammar; not authenticated HTTP/PG acceptance."""

from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401

from backend.app.engine_run_submission_schemas import RDSubmissionRequest
from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest, RDSeededSubmit
from backend.app.rdagent_seed_http import parse_submission


def test_original_default_stays_fresh_and_parent_never_selects_new_family(request_body):  # noqa:F811
    document = request_body.model_dump(mode="json")
    document.pop("schema_version")
    assert type(parse_submission(document, submitted=False)) is RDSubmissionRequest
    document["parent_factor_version_id"] = str(uuid4())
    with pytest.raises(ValueError):
        parse_submission(document, submitted=False)


def test_seeded_family_requires_exact_version_and_complete_parent(request_body):  # noqa:F811
    document = {
        **request_body.model_dump(mode="json"),
        "schema_version": "mineagent.rdagent-seeded-submission.v1",
        "parent_factor_version_id": str(uuid4()),
    }
    assert (
        type(parse_submission(document, submitted=False)) is RDSeededSubmissionRequest
    )
    submitted = {**document, "preflight_sha256": "a" * 64}
    assert type(parse_submission(submitted, submitted=True)) is RDSeededSubmit
    for key in ("parent_factor_version_id", "preflight_sha256"):
        malformed = dict(submitted)
        malformed.pop(key)
        with pytest.raises(ValueError):
            parse_submission(malformed, submitted=True)
    document["schema_version"] = "mineagent.rdagent-seeded-submission.v2"
    with pytest.raises(ValueError):
        parse_submission(document, submitted=False)
