"""Admission transaction doubles, not installed race acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_admission as service
from backend.app.services import DomainError
from packages.research_workflow.admission import CreateWorkflow, SelectWorkflowInputs
from tests.research_workflow.test_plan import plan


class Session:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.added = []
        self.flushed = False

    def scalar(self, statement):
        return next(self.rows)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushed = True


@pytest.fixture(autouse=True)
def guards(monkeypatch):
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *args: None)
    monkeypatch.setattr(service, "resolve_model_input_versions", lambda *args: ())


def test_create_is_real_persistence_but_no_fake_child():
    p = plan()
    session = Session([None])
    result = service.create_workflow(
        session, p.owner_id, CreateWorkflow(idempotency_key="new", plan=p), NS()
    )
    assert session.flushed
    assert len(session.added) == 2
    assert result.status == "blocked"
    assert result.current_stage_id is None
    assert session.added[0].blocked_reason == "workflow_child_adapter_unavailable"
    assert session.added[1].id == result.input_revision_id


def test_exact_creation_retry_returns_original_receipt_after_mutable_progress():
    p = plan()
    request = CreateWorkflow(idempotency_key="new", plan=p)
    session = Session([None])
    expected = service.create_workflow(session, p.owner_id, request, NS())
    root = session.added[0]
    root.revision = 19
    root.status = "paused"
    retry = Session([root, session.added[1]])
    assert service.create_workflow(retry, p.owner_id, request, NS()) == expected
    assert retry.added == []


def root():
    return NS(
        owner_id=uuid4(),
        id=uuid4(),
        revision=4,
        status="running",
        current_input_revision_id=uuid4(),
        current_stage_id=uuid4(),
        latest_checkpoint_id=None,
    )


def test_future_selection_preserves_running_stage_and_status():
    row = root()
    stage = row.current_stage_id
    prior_input = row.current_input_revision_id
    request = SelectWorkflowInputs(
        workflow_id=row.id, expected_revision=4, idempotency_key="select"
    )
    session = Session([row, None])
    result = service.select_workflow_inputs(session, row.owner_id, request, NS())
    assert row.current_stage_id == stage and row.status == "running"
    assert row.current_input_revision_id != prior_input
    assert result.revision == 5
    assert len(session.added) == 2
    retry = Session([row, session.added[1]])
    assert service.select_workflow_inputs(retry, row.owner_id, request, NS()) == result
    assert retry.added == []


def test_stale_selection_cannot_resolve_or_append(monkeypatch):
    row = root()
    monkeypatch.setattr(
        service,
        "resolve_model_input_versions",
        lambda *args: pytest.fail("must reject before read"),
    )
    request = SelectWorkflowInputs(
        workflow_id=row.id, expected_revision=3, idempotency_key="select"
    )
    session = Session([row, None])
    with pytest.raises(DomainError):
        service.select_workflow_inputs(session, row.owner_id, request, NS())
    assert session.added == []
    assert row.revision == 4


def test_creation_wrong_owner_rejected():
    with pytest.raises(DomainError):
        service.create_workflow(
            Session([]),
            uuid4(),
            CreateWorkflow(idempotency_key="new", plan=plan()),
            NS(),
        )


def test_external_stage_requires_owned_retained_bytes_and_extra_budget(monkeypatch):
    from packages.factors.contracts import digest
    from packages.research_inputs.derivation import ExternalNormalizationDefinition
    from packages.research_runtime.budget_contracts import BudgetVector

    p = plan()
    p = p.model_copy(
        update={
            "normalize_external_first": True,
            "budget_ceiling": p.budget_ceiling.add(BudgetVector(storage_bytes=294912)),
        }
    )
    definition = ExternalNormalizationDefinition.model_validate_json(
        '{"raw_object_id":"'
        + str(uuid4())
        + '","raw_sha256":"'
        + "a" * 64
        + '","mapping":{}}'
    )
    obj = NS(
        id=definition.raw_object_id,
        owner_id=p.owner_id,
        kind="raw_external",
        sha256=definition.raw_sha256,
    )
    seen = []
    monkeypatch.setattr(
        service,
        "read_input_object",
        lambda root, row, owner: seen.append((row.id, owner)),
    )
    session = Session([None, obj])
    result = service.create_workflow(
        session,
        p.owner_id,
        CreateWorkflow(
            idempotency_key="normalize", plan=p, external_definition=definition
        ),
        NS(storage_root="unused"),
    )
    assert seen == [(obj.id, p.owner_id)]
    assert len(session.added) == 3
    assert result.status == "ready"
    stage = session.added[2]
    assert stage.id == result.current_stage_id
    assert stage.child_run_id is None
    assert stage.definition_sha256 == digest(definition)
    assert stage.input_revision_id == result.input_revision_id


def test_create_key_conflict_does_not_rebind_initial_plan():
    p = plan()
    original = CreateWorkflow(idempotency_key="new", plan=p)
    session = Session([None])
    service.create_workflow(session, p.owner_id, original, NS())
    changed = original.model_copy(
        update={"plan": p.model_copy(update={"mode": "pause_after_stage"})}
    )
    with pytest.raises(DomainError):
        service.create_workflow(Session([session.added[0]]), p.owner_id, changed, NS())


def test_normalization_incremental_allocation_matches_real_publisher_bounds():
    import json

    from backend.app.research_input_dispatch import (
        PUBLICATION_PEAK_BYTES,
        PUBLICATION_PEAK_FILES,
    )
    from backend.app.research_input_storage import OBJECT_LIMITS
    from packages.factors.contracts import canonical_bytes
    from packages.research_inputs.contracts import ExternalFeedMapping
    from packages.research_inputs.normalization import (
        MAX_OUTPUT_BYTES,
        normalize_external_json,
    )

    result = normalize_external_json(
        json.dumps(
            {"items": [{"text": "a" * 1000, "title": str(n)} for n in range(32)]}
        ).encode(),
        ExternalFeedMapping(),
    )
    retained = sum(len(canonical_bytes(item)) for item in result.outputs)
    assert retained <= len(result.output_bytes) <= MAX_OUTPUT_BYTES
    assert all(
        len(canonical_bytes(item)) <= OBJECT_LIMITS["normalized_external"]
        for item in result.outputs
    )
    assert (
        PUBLICATION_PEAK_BYTES
        == MAX_OUTPUT_BYTES + OBJECT_LIMITS["normalized_external"]
        == 294912
    )
    assert PUBLICATION_PEAK_FILES == len(result.outputs) + 1 == 33
    # Raw already exists before this incremental publication; not charged twice.
    assert OBJECT_LIMITS["raw_external"] == 65536
