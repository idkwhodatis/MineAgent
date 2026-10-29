"""Exact successor dispatch and grammar, not full cold restore evidence."""

import hashlib
from uuid import uuid4

import pytest

from backend.app import research_backup as research
from packages.rdagent_adapter.execution_object_identity import object_identity


@pytest.mark.parametrize(
    "schema",
    [
        research.SCHEMA,
        research.SCHEMA_V2,
        research.SCHEMA_V3,
        research.SCHEMA_V4,
        research.SCHEMA_V5,
    ],
)
def test_historical_research_paths_reject_primitive_reports_and_typed_objects(schema):
    owner, admission = uuid4(), uuid4()
    report = f"research-processes/{owner}/{admission}/output/rows.json"
    typed = (
        f"research-objects/{owner}/by-kind/factor_generation_feedback/{'a' * 64}.json"
    )
    for path in (report, typed):
        with pytest.raises(ValueError):
            research.validate_path(path, schema=schema)
        research.validate_path(path, schema=research.SCHEMA_V6)


def test_v6_empty_schema_is_explicit_not_unknown_future_fallback():
    closure = {
        "schema_version": research.SCHEMA_V6,
        "tables": {k: [] for k in research.FIELDS},
        "external_bindings": {"runs": [], "attempts": []},
    }
    research.validate_closure(
        closure, lambda *a: None, lambda *a: None, 100, inventory=[]
    )
    assert research.closure_fields(research.SCHEMA_V10)
    for unknown in (
        "mineagent.research-restore-closure.v17",
        "mineagent.research-restore-closure.v1000",
    ):
        with pytest.raises(ValueError):
            research.closure_fields(unknown)


def test_v6_registered_typed_body_must_parse_even_without_final_generation():
    owner = uuid4()
    payload = {"not_a_typed_document": True}
    raw = research.canonical(payload)
    sha = hashlib.sha256(raw).hexdigest()
    identity = object_identity(owner, "factor_generation_feedback", sha)
    row = {
        "id": str(uuid4()),
        "owner_id": str(owner),
        "object_kind": "factor_generation_feedback",
        "sha256": sha,
        "size_bytes": len(raw),
        "storage_key": identity.storage_key,
        "media_type": identity.media_type,
    }
    indexes = {
        "research_objects": {row["id"]: row},
        "research_object_publications": {},
        "research_object_outcomes": {},
        "research_budget_settlements": {},
    }
    with pytest.raises(ValueError):
        research._validate_objects(
            indexes,
            {},
            lambda *a: None,
            lambda *a: payload,
            rd_source=True,
            rd_execution=True,
        )
