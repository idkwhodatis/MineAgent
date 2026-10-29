"""Retained workflow metadata unit checks; no physical/cold activation claim."""

import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_admission as admission
from backend.app import research_workflow_backup as service
from packages.research_workflow.admission import CreateWorkflow
from tests.research_workflow.test_admission import Session
from tests.research_workflow.test_plan import plan


def empty():
    return {
        "schema_version": service.SCHEMA,
        "tables": {name: [] for name in service.FIELDS},
    }


def test_literal_seven_table_inventory():
    from backend.app import rdagent_workflow_models, research_workflow_models  # noqa:F401
    from backend.app.models import Base

    names = {
        name
        for name in Base.metadata.tables
        if name.startswith("research_workflow") or name == "factor_workflow_submissions"
    }
    assert set(service.FIELDS) == names and len(names) == 7
    for name in names:
        assert set(service.FIELDS[name].split()) == set(
            Base.metadata.tables[name].columns.keys()
        )


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setattr(admission, "require_schema", lambda s: None)
    monkeypatch.setattr(admission, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(admission, "resolve_model_input_versions", lambda *a: ())
    p = plan()
    session = Session([None])
    admission.create_workflow(
        session,
        p.owner_id,
        CreateWorkflow(idempotency_key="cold-original", plan=p),
        NS(),
    )
    closure = empty()
    for row in session.added:
        fields = service.FIELDS[row.__tablename__].split()
        closure["tables"][row.__tablename__].append(
            json.loads(
                json.dumps({k: getattr(row, k, None) for k in fields}, default=str)
            )
        )
    return closure


@pytest.mark.parametrize(
    "change",
    [
        None,
        "schema",
        "missing_table",
        "extra_field",
        "head",
        "owner",
        "plan_hash",
        "original_result",
        "completed",
        "revision_gap",
        "orphan",
    ],
)
def test_original_creation_and_selected_head_are_not_execution_proof(declared, change):
    root = declared["tables"]["research_workflows"][0]
    revision = declared["tables"]["research_workflow_revisions"][0]
    if change == "schema":
        declared["schema_version"] = "unknown"
    elif change == "missing_table":
        declared["tables"].pop("factor_workflow_submissions")
    elif change == "extra_field":
        root["verified"] = True
    elif change == "head":
        root["current_input_revision_id"] = str(uuid4())
    elif change == "owner":
        revision["owner_id"] = str(uuid4())
    elif change == "plan_hash":
        root["plan_sha256"] = "0" * 64
    elif change == "original_result":
        root["creation_result_canonical"] = "{}"
    elif change == "completed":
        root["status"] = "completed"
    elif change == "revision_gap":
        revision["ordinal"] = 2
    elif change == "orphan":
        revision["workflow_id"] = str(uuid4())
    if change:
        with pytest.raises(ValueError):
            service.validate_metadata(declared)
    else:
        result = service.validate_metadata(declared)
        assert set(result["plans"]) == {root["id"]}
        assert result["checkpoints"] == result["definitions"] == {}
        assert root["status"] == "blocked"


def test_empty_graph_has_no_implicit_success():
    facts = service.validate_metadata(empty())
    assert facts["plans"] == facts["checkpoints"] == {}


def test_row_bound_applies_across_tables(declared):
    with pytest.raises(ValueError, match="bounded"):
        service.validate_metadata(declared, maximum_rows=1)


def command_row(closure, *, expected=1):
    from uuid import UUID

    from packages.factors.contracts import canonical_bytes, digest
    from packages.research_workflow.contracts import (
        WorkflowCommand,
        WorkflowCommandReceipt,
    )

    root = closure["tables"]["research_workflows"][0]
    command = WorkflowCommand(
        workflow_id=UUID(root["id"]),
        expected_revision=expected,
        idempotency_key=f"pause-{expected}",
        action="pause_after_stage",
    )
    receipt = WorkflowCommandReceipt(
        workflow_id=command.workflow_id,
        command_id=uuid4(),
        command_sha256=digest(command),
        revision=expected + 1,
        status="paused",
        checkpoint_id=None,
        current_stage_id=None,
    )
    row = {
        "id": str(receipt.command_id),
        "owner_id": root["owner_id"],
        "workflow_id": root["id"],
        "idempotency_key": command.idempotency_key,
        "command_canonical": canonical_bytes(command).decode(),
        "command_sha256": digest(command),
        "result_canonical": canonical_bytes(receipt).decode(),
        "created_at": None,
    }
    closure["tables"]["research_workflow_commands"].append(row)
    root["revision"] = expected + 1
    root["status"] = "paused"
    return row


@pytest.mark.parametrize(
    "change", [None, "hash", "foreign", "receipt", "duplicate", "unknown", "future"]
)
def test_retained_control_is_exact_ack_not_current_authority(declared, change):
    row = command_row(declared)
    if change == "hash":
        row["command_sha256"] = "0" * 64
    elif change == "foreign":
        row["owner_id"] = str(uuid4())
    elif change == "receipt":
        row["result_canonical"] = "{}"
    elif change == "duplicate":
        declared["tables"]["research_workflow_commands"].append(
            {**row, "id": str(uuid4())}
        )
    elif change == "unknown":
        row["command_canonical"] = row["command_canonical"].replace(
            "workflow-command.v1", "workflow-command.v999"
        )
    elif change == "future":
        declared["tables"]["research_workflows"][0]["revision"] = 1
    if change is None:
        assert service.validate_metadata(declared)["indexes"][
            "research_workflow_commands"
        ]
    else:
        with pytest.raises(ValueError):
            service.validate_metadata(declared)


def test_selection_ordinals_are_root_revisions_not_contiguous_sequence(declared):
    from uuid import UUID

    from packages.factors.contracts import canonical_bytes, digest
    from packages.research_workflow.contracts import WorkflowInputSnapshot

    command_row(declared)
    root = declared["tables"]["research_workflows"][0]
    original = declared["tables"]["research_workflow_revisions"][0]
    snapshot = WorkflowInputSnapshot(
        owner_id=UUID(root["owner_id"]),
        workflow_id=UUID(root["id"]),
        revision_id=uuid4(),
    )
    declared["tables"]["research_workflow_revisions"].append(
        {
            **original,
            "id": str(snapshot.revision_id),
            "ordinal": 3,
            "inputs_canonical": canonical_bytes(snapshot).decode(),
            "inputs_sha256": digest(snapshot),
        }
    )
    root["revision"] = 3
    root["current_input_revision_id"] = str(snapshot.revision_id)
    assert len(service.validate_metadata(declared)["selections"]) == 2
