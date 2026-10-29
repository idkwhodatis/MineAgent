"""Actual attachment reader over ORM projections, not PostgreSQL graph proof.

SQLite tables deliberately omit PostgreSQL constraints/triggers, so corrupted
rows can exercise every reader guard. Actual schema probe and ORM field names
are used; no attachment helper or schema checker is replaced.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import DateTime, Integer, create_engine, text
from sqlalchemy.orm import Session

from backend.app.models import ResearchRun
from backend.app.oe_structured_models import OEStructuredSpec, OEStructuredSubmission
from backend.app.oe_workflow_metadata import require_attachment
from backend.app.oe_workflow_models import OEWorkflowSubmission
from backend.app.research_workflow_models import (
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_structured_public import REVISION
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from tests.openevolve.test_workflow_run import config as config_raw

MODELS = (
    OEWorkflowSubmission,
    ResearchWorkflowStage,
    ResearchWorkflowRevision,
    OEStructuredSubmission,
)


def projection(model, **values):
    defaults = {}
    for column in model.__table__.columns:
        if column.name in values:
            continue
        if isinstance(column.type, DateTime):
            value = datetime.now(UTC)
        elif isinstance(column.type, Integer):
            value = 1
        elif column.type.python_type is UUID:
            value = uuid4()
        else:
            value = "a" * 64 if column.name.endswith("sha256") else "{}"
        defaults[column.name] = value
    return model(**defaults, **values)


@pytest.fixture
def arranged():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:v)"), {"v": REVISION}
        )
        for model in MODELS:
            columns = [
                column.name
                + (" INTEGER" if isinstance(column.type, Integer) else " TEXT")
                + (" PRIMARY KEY" if column.primary_key else "")
                for column in model.__table__.columns
            ]
            connection.execute(
                text(f"CREATE TABLE {model.__tablename__} ({','.join(columns)})")
            )
    config = WorkflowStructuredRunConfig.model_validate_json(
        canonical_bytes(config_raw())
    )
    ctx = config.workflow_context
    bound = ctx.inputs.binding
    run = ResearchRun(id=config.search.run_id, owner_id=config.search.owner_id)
    spec = OEStructuredSpec(run_id=run.id, owner_id=run.owner_id)
    stage = projection(
        ResearchWorkflowStage,
        id=bound.stage_id,
        owner_id=run.owner_id,
        workflow_id=bound.workflow_id,
        child_run_id=run.id,
        input_revision_id=bound.input_revision_id,
        definition_sha256=ctx.definition_sha256,
        kind="structured_factor_execution",
    )
    revision = projection(
        ResearchWorkflowRevision,
        id=bound.input_revision_id,
        owner_id=run.owner_id,
        workflow_id=bound.workflow_id,
        inputs_sha256=ctx.inputs.selection.sha256,
    )
    submission = projection(
        OEStructuredSubmission,
        owner_id=run.owner_id,
        run_id=run.id,
        origin="workflow_stage",
        request_sha256=config.workflow_request_sha256,
    )
    attachment = projection(
        OEWorkflowSubmission,
        owner_id=run.owner_id,
        run_id=run.id,
        workflow_id=bound.workflow_id,
        stage_id=stage.id,
        input_revision_id=revision.id,
        submission_id=submission.id,
        config_sha256=config.sha256,
        request_sha256=config.workflow_request_sha256,
        definition_sha256=ctx.definition_sha256,
        selection_sha256=ctx.inputs.selection.sha256,
        resolved_inputs_sha256=ctx.inputs.sha256,
        fixed_inputs_sha256=digest(config.fixed_inputs),
        submission_origin="workflow_stage",
        **{
            key: getattr(ctx, key)
            for key in (
                "rd_stage_id",
                "rd_checkpoint_id",
                "rd_checkpoint_sha256",
                "source_run_id",
                "source_attempt_id",
                "source_generation",
                "snapshot_id",
                "factor_version_id",
                "analysis_id",
                "release_exposure_id",
            )
        },
    )
    with Session(engine) as session:
        session.add_all([stage, revision, submission, attachment])
        identifiers = tuple(
            (type(row), row.id) for row in (stage, revision, submission, attachment)
        )
        session.commit()
        # Force ORM loading from real rows rather than identity-map-only checks.
        session.expunge_all()
        rows = {
            model: session.get(model, identifier) for model, identifier in identifiers
        }
        yield session, run, spec, config, rows
    engine.dispose()


def test_exact_attachment_uses_real_submission_origin_and_schema(arranged):
    session, run, spec, config, rows = arranged
    row = require_attachment(session, run, spec, config)
    assert row is rows[OEWorkflowSubmission]
    assert rows[OEStructuredSubmission].origin == "workflow_stage"
    assert row.resolved_inputs_sha256 == config.workflow_context.inputs.sha256
    assert require_attachment(session, run, spec, config) is row


@pytest.mark.parametrize(
    "versions",
    [[], ["030_declared_upload_basket"], [REVISION, REVISION], ["032_future"]],
)
def test_schema_gate_requires_exact_single_031(arranged, versions):
    session, run, spec, config, _ = arranged
    session.execute(text("DELETE FROM alembic_version"))
    for version in versions:
        session.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})
    with pytest.raises(DomainError):
        require_attachment(session, run, spec, config)


@pytest.mark.parametrize("kind", ["missing", "duplicate"])
def test_attachment_inventory_is_unique(arranged, kind):
    session, run, spec, config, rows = arranged
    row = rows[OEWorkflowSubmission]
    if kind == "missing":
        session.delete(row)
    else:
        values = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        session.add(OEWorkflowSubmission(**dict(values, id=uuid4())))
    session.flush()
    with pytest.raises(ValueError, match="Unique actual"):
        require_attachment(session, run, spec, config)


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "run_id",
        "workflow_id",
        "stage_id",
        "input_revision_id",
        "config_sha256",
        "request_sha256",
        "definition_sha256",
        "selection_sha256",
        "resolved_inputs_sha256",
        "fixed_inputs_sha256",
        "rd_stage_id",
        "rd_checkpoint_id",
        "rd_checkpoint_sha256",
        "source_run_id",
        "source_attempt_id",
        "source_generation",
        "snapshot_id",
        "factor_version_id",
        "analysis_id",
        "release_exposure_id",
        "submission_origin",
    ],
)
def test_each_attachment_commitment_is_checked(arranged, field):
    session, run, spec, config, rows = arranged
    row = rows[OEWorkflowSubmission]
    old = getattr(row, field)
    new = (
        uuid4()
        if isinstance(old, UUID)
        else old + 1
        if type(old) is int
        else ("c" * 64 if old == "b" * 64 else "b" * 64)
    )
    if field == "submission_origin":
        new = "authenticated_user_submission"
    setattr(row, field, new)
    session.flush()
    with pytest.raises(ValueError):
        require_attachment(session, run, spec, config)


@pytest.mark.parametrize(
    "model,field",
    [
        (ResearchWorkflowStage, "owner_id"),
        (ResearchWorkflowStage, "workflow_id"),
        (ResearchWorkflowStage, "child_run_id"),
        (ResearchWorkflowStage, "input_revision_id"),
        (ResearchWorkflowStage, "definition_sha256"),
        (ResearchWorkflowStage, "kind"),
        (ResearchWorkflowRevision, "owner_id"),
        (ResearchWorkflowRevision, "workflow_id"),
        (ResearchWorkflowRevision, "inputs_sha256"),
        (OEStructuredSubmission, "owner_id"),
        (OEStructuredSubmission, "run_id"),
        (OEStructuredSubmission, "origin"),
        (OEStructuredSubmission, "request_sha256"),
    ],
)
def test_related_actual_orm_rows_cannot_drift(arranged, model, field):
    session, run, spec, config, rows = arranged
    old = getattr(rows[model], field)
    new = (
        uuid4()
        if isinstance(old, UUID)
        else ("c" * 64 if old == "b" * 64 else "b" * 64)
    )
    if field == "origin":
        new = "authenticated_user_submission"
    setattr(rows[model], field, new)
    session.flush()
    with pytest.raises(ValueError, match="relation differs"):
        require_attachment(session, run, spec, config)


@pytest.mark.parametrize("model", MODELS[1:])
def test_missing_related_row_rejected(arranged, model):
    session, run, spec, config, rows = arranged
    session.delete(rows[model])
    session.flush()
    with pytest.raises(ValueError, match="relation differs"):
        require_attachment(session, run, spec, config)


def test_wrong_spec_child_rejected(arranged):
    session, run, spec, config, _ = arranged
    spec.run_id = uuid4()
    with pytest.raises(ValueError, match="relation differs"):
        require_attachment(session, run, spec, config)


def test_dictionary_cannot_replace_typed_workflow_config(arranged):
    session, run, spec, config, _ = arranged
    with pytest.raises(ValueError, match="Exact workflow OE config"):
        require_attachment(session, run, spec, config.model_dump(mode="json"))
