"""Immutable decreasing metadata lineage; full physical reader tested separately."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_rd_predecessor as service
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import InputVersionRef, SourceOutputRef
from packages.research_inputs.feedback import FeedbackProjectionDefinition
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.feedback_checkpoint import (
    FeedbackCheckpoint,
    FeedbackCheckpointObject,
)
from packages.research_workflow.rd_stage import RDStageDefinition
from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.fixture
def chain(boundary, monkeypatch):  # noqa: F811
    b = boundary
    facts = b.actual.model_copy(
        update={
            "owner_id": b.root.owner_id,
            "definition_sha256": b.stage.definition_sha256,
        }
    )
    b.stage.child_run_id = facts.child_run_id
    rdrow = NS(
        id=uuid4(),
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        stage_id=b.stage.id,
        terminal_canonical=canonical_bytes(facts).decode(),
        terminal_sha256=digest(facts),
    )
    selection = WorkflowInputSnapshot(
        owner_id=b.root.owner_id, workflow_id=b.root.id, revision_id=uuid4()
    )
    revision = NS(
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    source = SourceOutputRef(
        workflow_id=b.root.id,
        stage_id=b.stage.id,
        child_run_id=facts.child_run_id,
        output_kind="direct_analysis",
        output_id=facts.analysis_id,
        content_sha256=facts.analysis_result_sha256,
    )
    definition = FeedbackProjectionDefinition(source=source)
    feedback = NS(
        id=uuid4(),
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        kind="experiment_feedback_projection",
        ordinal=2,
        input_revision_id=selection.revision_id,
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=digest(definition),
        allocation_sha256=digest(BudgetVector(storage_bytes=65536)),
    )
    projection = FeedbackCheckpoint(
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        stage_id=feedback.id,
        input_revision_id=selection.revision_id,
        input_sha256=selection.sha256,
        definition_sha256=feedback.definition_sha256,
        allocation_sha256=feedback.allocation_sha256,
        rd_checkpoint_id=rdrow.id,
        source=source,
        derivation_id=uuid4(),
        terminal_event_id=uuid4(),
        terminal_event_sha256="a" * 64,
        state="succeeded",
        outputs=(
            InputVersionRef(
                origin="experiment_feedback_proposal",
                record_id=uuid4(),
                version_id=uuid4(),
                content_sha256="b" * 64,
            ),
        ),
        objects=(
            FeedbackCheckpointObject(id=uuid4(), sha256="b" * 64, size_bytes=123),
        ),
        exposure_ids=(uuid4(), uuid4()),
    )
    feedbackrow = NS(
        id=uuid4(),
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        stage_id=feedback.id,
        terminal_canonical=canonical_bytes(projection).decode(),
        terminal_sha256=digest(projection),
    )
    nextdef = RDStageDefinition(
        plan_sha256=b.root.plan_sha256,
        research_iteration=1,
        predecessor_checkpoint_id=feedbackrow.id,
        parent_factor_version_id=facts.factor_version_id,
        parent_source_stage_id=b.stage.id,
    )
    stage = NS(
        id=uuid4(),
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        kind="rd_factor_generation",
        ordinal=3,
        definition_canonical=canonical_bytes(nextdef).decode(),
        definition_sha256=nextdef.sha256,
    )
    rows = iter((b.root, feedbackrow, revision, rdrow))
    session = NS(
        scalar=lambda q: next(rows), scalars=lambda q: [b.stage, feedback, stage]
    )
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    return NS(
        b=b,
        facts=facts,
        stage=stage,
        feedback=feedback,
        definition=nextdef,
        feedbackrow=feedbackrow,
        rdrow=rdrow,
        projection=projection,
        session=session,
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "parent",
        "iteration",
        "source_stage",
        "feedback_hash",
        "foreign",
        "current_head_changed",
    ],
)
def test_only_exact_decreasing_prior_child_and_released_projection(chain, change):
    c = chain
    if change in {"parent", "iteration", "source_stage"}:
        updates = {
            "parent": {"parent_factor_version_id": uuid4()},
            "iteration": {"research_iteration": 0},
            "source_stage": {"parent_source_stage_id": c.stage.id},
        }[change]
        value = c.definition.model_copy(update=updates)
        c.stage.definition_canonical = canonical_bytes(value).decode()
        c.stage.definition_sha256 = value.sha256
    elif change == "feedback_hash":
        c.feedbackrow.terminal_sha256 = "c" * 64
    elif change == "foreign":
        c.rdrow.owner_id = uuid4()
    elif change == "current_head_changed":
        c.b.root.current_stage_id = uuid4()
        c.b.root.status = "completed"
    if change and change != "current_head_changed":
        with pytest.raises((DomainError, ValueError)):
            service.verify_rd_predecessor_metadata(
                c.session, c.b.root.owner_id, c.b.root.id, c.stage.id
            )
    else:
        value = service.verify_rd_predecessor_metadata(
            c.session, c.b.root.owner_id, c.b.root.id, c.stage.id
        )
        assert value.checkpoint == c.facts and value.parent_stage_id == c.b.stage.id


@pytest.mark.parametrize(
    "change", [None, "rd_changed", "feedback_hash", "exposure", "missing_guard"]
)
def test_full_predecessor_rechecks_actual_child_and_feedback_under_guard(
    chain, monkeypatch, tmp_path, change
):
    from backend.app import research_input_feedback
    from backend.app.factor_native_io import NativeStorageError, native_storage_guard

    c = chain
    value = service.verify_rd_predecessor_metadata(
        c.session, c.b.root.owner_id, c.b.root.id, c.stage.id
    )
    monkeypatch.setattr(service, "verify_rd_predecessor_metadata", lambda *a: value)
    actual = (
        value.checkpoint
        if change != "rd_changed"
        else value.checkpoint.model_copy(update={"factor_version_id": uuid4()})
    )
    monkeypatch.setattr(service, "verify_rd_checkpoint", lambda *a: (c.rdrow, actual))
    saved = value.feedback_checkpoint
    facts = NS(
        derivation_id=saved.derivation_id,
        terminal_event_id=saved.terminal_event_id,
        terminal_event_sha256=saved.terminal_event_sha256,
        state="succeeded",
        outputs=saved.outputs,
        objects=saved.objects,
        exposure_ids=saved.exposure_ids,
    )
    if change == "feedback_hash":
        facts.terminal_event_sha256 = "d" * 64
    elif change == "exposure":
        facts.exposure_ids = (uuid4(), uuid4())
    monkeypatch.setattr(
        research_input_feedback,
        "validate_feedback_terminal_under_native_guard",
        lambda *a: facts,
    )
    settings = NS(storage_root=tmp_path)
    (tmp_path / "factors").mkdir()
    if change == "missing_guard":
        with pytest.raises(NativeStorageError):
            service.verify_rd_predecessor(
                c.session, c.b.root.owner_id, c.b.root.id, c.stage.id, settings
            )
    else:
        with native_storage_guard(tmp_path):
            if change:
                with pytest.raises(DomainError):
                    service.verify_rd_predecessor(
                        c.session, c.b.root.owner_id, c.b.root.id, c.stage.id, settings
                    )
            else:
                assert (
                    service.verify_rd_predecessor(
                        c.session, c.b.root.owner_id, c.b.root.id, c.stage.id, settings
                    )
                    == value
                )
