"""Retained bounded native evidence grammar, not semantic/financial authority."""

import hashlib
from uuid import uuid4

import pytest

from packages.openevolve_adapter.checkpoint_native import canonical, validate_directory
from packages.openevolve_adapter.object_identity import (
    identity_for_content,
    parse_document,
    parse_storage_key,
)
from tests.openevolve.test_checkpoint_native import native  # noqa:F401


def objective():
    feedback = canonical(
        {"status": "eligible", "combined_score": -3.125, "reason": "eligible"}
    ).decode()
    return {
        "schema_version": "mineagent.search-objective-object.v1",
        "candidate_id": str(uuid4()),
        "source_sha256": "a" * 64,
        "objective_sha256": "b" * 64,
        "evaluation_ids": [str(uuid4())],
        "status": "eligible",
        "combined_score": -3.125,
        "reason": "eligible",
        "feedback": feedback,
        "feedback_sha256": hashlib.sha256(feedback.encode()).hexdigest(),
    }


def test_actual_population_projection_and_identity(native):  # noqa: F811
    raw = canonical(validate_directory(native))
    owner = uuid4()
    parsed = parse_document("search_population", raw)
    assert parsed["metadata"]["last_iteration"] == 0
    identity = identity_for_content(owner, "search_population", raw)
    assert parse_storage_key(identity.storage_key) == (
        "object",
        owner,
        "search_population",
        hashlib.sha256(raw).hexdigest(),
        False,
    )


def test_float_objective_preserves_exact_bytes():
    value = objective()
    raw = canonical(value)
    assert parse_document("search_objective", raw) == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("combined_score", True),
        ("status", "eligible_verified"),
        ("reason", "unknown"),
        ("feedback", "forged"),
        ("feedback_sha256", "0" * 64),
        ("source_sha256", "x"),
        ("evaluation_ids", []),
        ("extra", 1),
    ],
)
def test_objective_failclosed(field, value):
    document = objective()
    document[field] = value
    with pytest.raises(ValueError):
        parse_document("search_objective", canonical(document))


def test_consistent_but_wrong_feedback_rejected():
    document = objective()
    document["feedback"] = "{}"
    document["feedback_sha256"] = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ValueError):
        parse_document("search_objective", canonical(document))


def test_source_exact_utf8_hash():
    source = "# 测试\nx=1\n"
    value = {
        "schema_version": "mineagent.search-source.v1",
        "source": source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
    }
    assert parse_document("search_source", canonical(value)).source == source


def test_worker_outcome_needs_actual_ack():
    value = {
        "schema_version": "mineagent.search-worker-outcome.v1",
        "epoch_id": str(uuid4()),
        "intent_id": str(uuid4()),
        "native_iteration": 1,
        "status": "evaluated",
        "objective_ack_id": None,
    }
    with pytest.raises(ValueError):
        parse_document("search_worker_outcome", canonical(value))
    value["objective_ack_id"] = str(uuid4())
    assert parse_document("search_worker_outcome", canonical(value)) == value


def test_duplicate_json_not_accepted():
    with pytest.raises(ValueError):
        parse_document(
            "search_native_failure", b'{"evaluation_id":"a","evaluation_id":"b"}'
        )
