"""Retained source projection tests; not installed PostgreSQL acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_history as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_workflow.contracts import WorkflowInputSnapshot


class Session:
    def __init__(self, rows):
        self.rows = iter(rows)

    def scalar(self, statement):
        return next(self.rows)


@pytest.fixture
def graph(monkeypatch):
    owner, workflow, stage_id, revision_id = (uuid4() for _ in range(4))
    selection = WorkflowInputSnapshot(
        owner_id=owner, workflow_id=workflow, revision_id=revision_id
    )
    definition = ExternalNormalizationDefinition.model_validate_json(
        '{"raw_object_id":"'
        + str(uuid4())
        + '","raw_sha256":"'
        + "a" * 64
        + '","mapping":{}}'
    )
    root = NS(id=workflow, owner_id=owner, current_input_revision_id=uuid4())
    stage = NS(
        id=stage_id,
        owner_id=owner,
        workflow_id=workflow,
        ordinal=1,
        kind="external_normalization",
        child_run_id=None,
        input_revision_id=revision_id,
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=digest(definition),
    )
    revision = NS(
        id=revision_id,
        owner_id=owner,
        workflow_id=workflow,
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    return owner, root, stage, revision


def read(graph, derivation=None):
    owner, root, stage, revision = graph
    return service.read_stage_history(
        Session([root, stage, revision, derivation, None]),
        owner,
        root.id,
        stage.id,
        NS(),
    )


def test_missing_receipt_is_not_prepared_and_later_selection_is_irrelevant(graph):
    result = read(graph)
    assert result.source_status == "not_recorded"
    assert result.consumption == "not_submitted_to_model"
    assert result.settlement == "not_observed"
    assert result.checkpoint_id is None


@pytest.mark.parametrize("field", ["inputs_sha256", "inputs_canonical"])
def test_corrupt_frozen_selection_rejected(graph, field):
    setattr(graph[3], field, "b" * 64 if field.endswith("sha256") else "{}")
    with pytest.raises(DomainError):
        read(graph)


def test_owned_old_engine_run_cannot_be_attached(graph):
    graph[2].kind = "rd_factor_generation"
    graph[2].child_run_id = uuid4()
    owner, root, stage, _revision = graph
    # Actual new-child receipt is absent; same-owner old run is not a binding.
    with pytest.raises(DomainError, match="Retained child"):
        service.read_stage_history(
            Session([root, stage, None]), owner, root.id, stage.id, NS()
        )


def test_wrong_scope_is_rejected(graph):
    graph[2].owner_id = uuid4()
    with pytest.raises(DomainError):
        read(graph)


def test_started_without_terminal_stays_uncertain(graph, monkeypatch):
    row = NS(id=uuid4())
    monkeypatch.setattr(service, "verify_derivation", lambda *args: None)
    monkeypatch.setattr(
        service,
        "read_derivation_history",
        lambda *args: NS(
            derivation_id=row.id,
            outputs=(),
            status="started_no_terminal_receipt",
            started_at=None,
            terminal_at=None,
            error_code=None,
        ),
    )
    result = read(graph, row)
    assert result.source_status == "started_no_terminal_receipt"
    assert result.finished_at is None
    assert result.containment == "not_applicable"
    assert result.settlement == "not_observed"


def test_success_outputs_are_historically_verified_without_returning_text(
    graph, monkeypatch
):
    from packages.research_inputs.contracts import InputVersionRef

    row = NS(id=uuid4())
    ref = InputVersionRef(
        origin="normalized_external_proposal",
        record_id=uuid4(),
        version_id=uuid4(),
        content_sha256="c" * 64,
    )
    seen = []
    monkeypatch.setattr(service, "verify_derivation", lambda *args: None)
    monkeypatch.setattr(
        service,
        "read_derivation_history",
        lambda *args: NS(
            derivation_id=row.id,
            outputs=(ref,),
            status="succeeded",
            started_at=None,
            terminal_at=None,
            error_code=None,
        ),
    )
    monkeypatch.setattr(
        service, "verify_model_input_versions", lambda *args: seen.append(args[2])
    )
    result = read(graph, row)
    assert seen == [(ref,)]
    assert result.derived_input_refs == (ref,)
    assert result.model_requests == ()
    assert result.output_refs == ()
    assert result.settlement == "not_observed"


def test_actual_root_read_qualifies_blocked_metadata_and_redacts_unknown_reason(
    monkeypatch,
):
    from tests.research_workflow.test_plan import plan

    p = plan()
    row = NS(
        id=uuid4(),
        owner_id=p.owner_id,
        plan_canonical=canonical_bytes(p).decode(),
        plan_sha256=p.sha256,
        current_stage_id=None,
        status="blocked",
        revision=1,
        mode=p.mode,
        current_input_revision_id=uuid4(),
        latest_checkpoint_id=None,
        blocked_reason="private diagnostic text",
    )
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    result = service.read_workflow(Session([row]), p.owner_id, row.id, NS())
    assert result.current_stage is None
    assert result.blocked_reason == "workflow_blocked"
    assert result.controls.resume is False
    assert result.controls.cancel is True


@pytest.mark.parametrize("tamper", [False, True])
def test_checkpoint_metadata_links_exact_terminal_event_not_just_status(
    graph, monkeypatch, tamper
):
    from packages.research_workflow.local_checkpoint import (
        LocalCheckpointObject,
        LocalNormalizationCheckpoint,
    )

    owner, root, stage, revision = graph
    stage.allocation_sha256 = "e" * 64
    derivation = NS(id=uuid4())
    terminal_id = uuid4()
    history = NS(
        derivation_id=derivation.id,
        terminal_event_id=terminal_id,
        terminal_event_sha256="f" * 64,
        status="rejected",
        outputs=(),
        started_at=None,
        terminal_at=None,
        error_code="invalid_json",
    )
    value = LocalNormalizationCheckpoint(
        owner_id=owner,
        workflow_id=root.id,
        stage_id=stage.id,
        input_revision_id=revision.id,
        input_sha256=revision.inputs_sha256,
        definition_sha256=stage.definition_sha256,
        allocation_sha256=stage.allocation_sha256,
        derivation_id=derivation.id,
        terminal_event_id=uuid4() if tamper else terminal_id,
        terminal_event_sha256=history.terminal_event_sha256,
        state="rejected",
        outputs=(),
        objects=(
            LocalCheckpointObject(
                id=ExternalNormalizationDefinition.model_validate_json(
                    stage.definition_canonical
                ).raw_object_id,
                kind="raw_external",
                sha256="a" * 64,
                size_bytes=1,
            ),
        ),
    )
    checkpoint = NS(
        id=uuid4(),
        owner_id=owner,
        workflow_id=root.id,
        stage_id=stage.id,
        terminal_canonical=canonical_bytes(value).decode(),
        terminal_sha256=digest(value),
    )
    monkeypatch.setattr(service, "verify_derivation", lambda *args: None)
    monkeypatch.setattr(service, "read_derivation_history", lambda *args: history)
    session = Session([root, stage, revision, derivation, checkpoint])
    if tamper:
        with pytest.raises(DomainError):
            service.read_stage_history(session, owner, root.id, stage.id, NS())
    else:
        result = service.read_stage_history(session, owner, root.id, stage.id, NS())
        assert result.checkpoint_id == checkpoint.id
        assert result.settlement == "not_observed"
