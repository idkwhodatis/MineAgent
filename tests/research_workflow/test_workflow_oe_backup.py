"""Versioned creation metadata is not execution completion or release authority."""

import json
from types import SimpleNamespace

import pytest
from test_structured_plan import combined

from backend.app import research_workflow_admission as admission
from backend.app import research_workflow_backup as cold
from backend.app import research_workflow_structured_public as public
from backend.app.research_workflow_structured_schemas import CreateStructuredWorkflow
from packages.research_workflow.structured_plan import RDStructuredGuidancePlan
from tests.research_workflow.test_admission import Session


@pytest.fixture
def structured_declared(monkeypatch):
    monkeypatch.setattr(admission, "require_schema", lambda s: None)
    monkeypatch.setattr(public, "require_structured_workflow_schema", lambda s: None)
    monkeypatch.setattr(admission, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(admission, "resolve_model_input_versions", lambda *a: ())
    from packages.factors.contracts import canonical_bytes

    plan = RDStructuredGuidancePlan.model_validate_json(canonical_bytes(combined()))
    session = Session([None])
    admission.create_workflow(
        session,
        plan.owner_id,
        CreateStructuredWorkflow(idempotency_key="cold-structured", plan=plan),
        SimpleNamespace(),
    )
    result = {
        "schema_version": cold.SCHEMA_V2,
        "tables": {name: [] for name in cold.FIELDS_V2},
    }
    for row in session.added:
        result["tables"][row.__tablename__].append(
            json.loads(
                json.dumps(
                    {
                        key: getattr(row, key, None)
                        for key in cold.FIELDS_V2[row.__tablename__].split()
                    },
                    default=str,
                )
            )
        )
    return result


@pytest.mark.parametrize(
    "change", [None, "old", "downgrade", "completed", "extra_relation", "bad_plan_hash"]
)
def test_explicit_new_plan_metadata_never_implies_completion(
    structured_declared, change
):
    root = structured_declared["tables"]["research_workflows"][0]
    if change == "old":
        with pytest.raises(ValueError):
            cold.validate_metadata(structured_declared)
        return
    if change == "downgrade":
        structured_declared["schema_version"] = cold.SCHEMA
        structured_declared["tables"].pop("oe_workflow_submissions")
        with pytest.raises(ValueError):
            cold.validate_metadata(structured_declared)
        return
    if change == "completed":
        root["status"] = "completed"
    elif change == "extra_relation":
        structured_declared["tables"]["oe_workflow_submissions"].append(
            {"id": root["id"]}
        )
    elif change == "bad_plan_hash":
        root["plan_sha256"] = "a" * 64
    if change:
        with pytest.raises(ValueError):
            cold.validate_metadata(structured_declared, schema=cold.SCHEMA_V2)
    else:
        facts = cold.validate_metadata(structured_declared, schema=cold.SCHEMA_V2)
        assert type(facts["plans"][root["id"]]) is RDStructuredGuidancePlan
        assert not facts["checkpoints"]
