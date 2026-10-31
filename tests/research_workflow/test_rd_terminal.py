"""Actual stage-boundary predicates with SQL doubles; not hosted child execution."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_rd_terminal as service
from backend.app.models import ExecutionAttempt
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector
from tests.rdagent.test_rdagent_workflow_child_contracts import config


@pytest.mark.parametrize(
    "change", [None, "missing", "uncertain", "refund", "unknown_measurement", "orphan"]
)
def test_all_actual_holds_must_be_fully_settled(change):
    held = NS(
        id=uuid4(), state="settled", amounts=BudgetVector(mutation_slots=1).model_dump()
    )
    paid = NS(
        reservation_id=held.id, outcome="conservative_unknown", charge=held.amounts
    )
    records = [paid]
    if change == "missing":
        records.clear()
    elif change == "uncertain":
        held.state = "uncertain"
    elif change == "refund":
        paid.charge = BudgetVector().model_dump()
    elif change == "unknown_measurement":
        paid.outcome = "measured"
    elif change == "orphan":
        records.append(NS(reservation_id=uuid4()))

    def rows(stmt):
        return (
            [held]
            if stmt.column_descriptions[0]["entity"] is ResearchBudgetReservation
            else records
        )

    session = NS(scalars=rows)
    if change:
        with pytest.raises(DomainError) as caught:
            service._settlements(session, {})
        assert caught.value.code == "workflow_waiting_containment"
    else:
        assert service._settlements(session, {}) == (held.id,)


@pytest.mark.parametrize(
    "missing", ["sdk_stop", "sdk_charge", "unfinished_attempt", "unknown_group"]
)
def test_succeeded_child_cannot_be_checkpoint_without_containment(monkeypatch, missing):
    value = config()
    binding = value.workflow_inputs.binding
    run = NS(
        id=value.workflow_child_run_id,
        owner_id=binding.owner_id,
        engine="rdagent",
        purpose="factor_research",
        status="succeeded",
        config=value.model_dump(mode="json"),
        active_attempt_id=uuid4(),
        generation=1,
    )
    attempt = NS(
        id=run.active_attempt_id,
        run_id=run.id,
        generation=1,
        finished_at=datetime.now(UTC),
        sandbox_stopped=True,
    )
    root = NS(id=binding.workflow_id)
    stage = NS(id=binding.stage_id, kind="rd_factor_generation", child_run_id=run.id)
    receipt = NS(id=uuid4(), run_id=run.id)
    if missing == "unfinished_attempt":
        attempt.finished_at = None
    elif missing == "unknown_group":
        attempt.sandbox_stopped = False
    models = {
        ResearchWorkflow: root,
        ResearchWorkflowStage: stage,
        FactorWorkflowSubmission: receipt,
    }
    monkeypatch.setattr(service, "_one", lambda session, model, **kw: models[model])
    monkeypatch.setattr(service, "require_schema", lambda session: None)
    monkeypatch.setattr(service, "require_native_storage_guard", lambda root: None)
    monkeypatch.setattr(
        service, "verify_workflow_submission_under_native_guard", lambda *a: receipt
    )
    monkeypatch.setattr(service, "provider_attempt_stopped", lambda *a: False)
    monkeypatch.setattr(
        service,
        "read_analysis_in_session",
        lambda *a: pytest.fail("uncontained child read"),
    )
    session = NS(scalar=lambda stmt: run, scalars=lambda stmt: [attempt])
    with pytest.raises(DomainError) as caught:
        service.read_rd_terminal_in_session(
            session, binding.owner_id, root.id, stage.id, NS(storage_root="/unused")
        )
    assert caught.value.code == "workflow_waiting_containment"


@pytest.fixture
def completed_graph(monkeypatch):
    import json

    from backend.app import rdagent_workflow_source_history
    from backend.app.execution_snapshot_models import (
        ExecutionSnapshotPublication,
        FactorExecutionEvaluationOutcome,
    )
    from backend.app.factor_analysis_models import (
        FactorDirectAnalysisIntent,
    )
    from backend.app.factor_models import FactorVersion
    from backend.app.rdagent_boundary_models import FactorGenerationBoundary
    from backend.app.rdagent_models import (
        FactorControllerLaunch,
        FactorEvaluationStart,
        FactorGenerationSource,
        FactorGenerationStart,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
    )
    from packages.factors.contracts import canonical_bytes, digest
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest
    from packages.rdagent_adapter.workflow_bootstrap import (
        RDWorkflowOperationalBootstrap,
    )
    from tests.factors.test_execution_snapshot_contract import manifest

    value = config()
    binding = value.workflow_inputs.binding
    m = manifest()
    raw = json.loads(
        canonical_bytes(m).decode().replace(str(m.owner_id), str(binding.owner_id))
    )
    raw["run_id"] = str(value.workflow_child_run_id)
    m = ExecutionSnapshotManifest.model_validate_json(json.dumps(raw))
    scope = {
        "owner_id": m.owner_id,
        "run_id": m.run_id,
        "attempt_id": m.attempt_id,
        "generation": 1,
    }
    attempt = NS(
        id=m.attempt_id,
        run_id=m.run_id,
        generation=1,
        finished_at=datetime.now(UTC),
        sandbox_stopped=True,
    )
    run = NS(
        id=m.run_id,
        owner_id=m.owner_id,
        engine="rdagent",
        purpose="factor_research",
        status="succeeded",
        config=value.model_dump(mode="json"),
        active_attempt_id=attempt.id,
        generation=1,
    )
    root = NS(id=binding.workflow_id)
    stage = NS(
        id=binding.stage_id,
        kind="rd_factor_generation",
        child_run_id=run.id,
        input_revision_id=binding.input_revision_id,
        definition_sha256=binding.definition_sha256,
        allocation_sha256="a" * 64,
    )
    receipt = NS(id=uuid4(), run_id=run.id, spec_id=uuid4(), plan_id=uuid4())
    start = NS(
        **scope,
        id=m.generation_start_id,
        spec_id=receipt.spec_id,
        logical_step=0,
        task_index=0,
        parent_factor_version_id=None,
    )
    source = NS(
        **scope,
        id=m.source_id,
        start_id=start.id,
        factor_version_id=m.input.version.factor_version_id,
        source_sha256=m.input.version.code_sha256,
    )
    version = NS(
        id=source.factor_version_id,
        owner_id=m.owner_id,
        parent_version_id=None,
        ordinal=1,
        factor_id=uuid4(),
        source_kind="rdagent",
        code_sha256=source.source_sha256,
    )
    evaluation = NS(
        **scope,
        id=m.evaluation_id,
        start_id=start.id,
        evaluated_source_id=source.id,
        factor_version_id=version.id,
    )
    evaluated = FactorExecutionEvaluationOutcome(
        **scope,
        id=uuid4(),
        evaluation_id=evaluation.id,
        generation_start_id=start.id,
        snapshot_id=m.snapshot_id,
        snapshot_manifest_sha256=digest(m),
        status="valid",
        snapshot_input_sha256=m.input_sha256,
        snapshot_output_sha256=m.output_rows_sha256,
    )
    publication = NS(
        **scope,
        id=m.snapshot_id,
        evaluation_id=evaluation.id,
        start_id=start.id,
        source_id=source.id,
        factor_version_id=version.id,
        manifest=m.model_dump(mode="json"),
        manifest_sha256=digest(m),
    )
    analysis = NS(
        **scope, id=uuid4(), plan_id=receipt.plan_id, snapshot_id=m.snapshot_id
    )
    outcome = NS(
        **scope,
        analysis_id=analysis.id,
        admission_id=uuid4(),
        result_sha256="b" * 64,
        measurement_sha256="c" * 64,
    )
    bootstrap = RDWorkflowOperationalBootstrap(
        **scope,
        spec_id=receipt.spec_id,
        attempt_binding_id=uuid4(),
        config=value,
        config_sha256=value.sha256,
        input_sha256=value.input.sha256,
        seed=None,
    )
    controller = NS(
        **scope,
        id=uuid4(),
        admission_id=uuid4(),
        bootstrap=bootstrap.model_dump(mode="json"),
        bootstrap_sha256=bootstrap.sha256,
    )
    boundary = FactorGenerationBoundary(
        **scope,
        id=uuid4(),
        start_id=start.id,
        controller_launch_id=controller.id,
        logical_step=0,
        boundary="returned",
        next_logical_step=None,
    )
    evaluated.boundary_id = boundary.id
    evaluated.controller_launch_id = controller.id
    admissions = [
        NS(
            **scope,
            id=controller.admission_id,
            role="rd_orchestrator",
            input_sha256=bootstrap.sha256,
            image_id=value.runtime.orchestrator_image,
        ),
        NS(**scope, id=outcome.admission_id, role="native_evaluator"),
    ]
    admissions += [
        NS(
            **scope,
            id=m.partitions[0].candidate_admission_id,
            role="factor_cutoff_candidate",
        ),
        NS(
            **scope,
            id=m.partitions[0].normalizer_admission_id,
            role="factor_normalizer",
        ),
    ]
    receipts = [
        NS(**scope, id=uuid4(), kind=kind, payload={}, payload_sha256="d" * 64)
        for kind in ("launch_intent", "launch_identity", "controller_child", "stop")
    ]
    reservation = NS(
        **scope,
        id=uuid4(),
        state="settled",
        amounts=BudgetVector(process_slots=4).model_dump(),
    )
    settlement = NS(
        **scope,
        reservation_id=reservation.id,
        outcome="conservative_unknown",
        charge=reservation.amounts,
    )
    by_model = {
        ResearchWorkflow: root,
        ResearchWorkflowStage: stage,
        FactorWorkflowSubmission: receipt,
        FactorGenerationStart: start,
        FactorGenerationSource: source,
        FactorEvaluationStart: evaluation,
        FactorExecutionEvaluationOutcome: evaluated,
        FactorGenerationBoundary: boundary,
        ExecutionSnapshotPublication: publication,
        FactorDirectAnalysisIntent: analysis,
        FactorVersion: version,
        FactorControllerLaunch: controller,
    }

    def one(session, model, **filters):
        row = by_model[model]
        if any(getattr(row, key, wanted) != wanted for key, wanted in filters.items()):
            service.unavailable()
        return row

    def many(stmt):
        model = stmt.column_descriptions[0]["entity"]
        return {
            ExecutionAttempt: [attempt],
            ResearchProcessAdmission: admissions,
            ResearchProcessReceipt: receipts,
            ResearchBudgetReservation: [reservation],
            ResearchBudgetSettlement: [settlement],
        }[model]

    session = NS(scalar=lambda stmt: run, scalars=many, get=lambda model, key: outcome)
    original_one = service._one
    checked = []
    monkeypatch.setattr(service, "_one", one)
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    monkeypatch.setattr(service, "require_native_storage_guard", lambda r: None)
    monkeypatch.setattr(
        service, "verify_workflow_submission_under_native_guard", lambda *a: receipt
    )
    monkeypatch.setattr(service, "controller_stop_committed", lambda *a: True)
    monkeypatch.setattr(
        service,
        "read_historical_controller_records",
        lambda *a, **k: checked.append("controller_bytes"),
    )
    monkeypatch.setattr(service, "provider_attempt_stopped", lambda *a: True)
    monkeypatch.setattr(
        rdagent_workflow_source_history,
        "verify_workflow_source_history",
        lambda *a: checked.append("source_bytes"),
    )
    monkeypatch.setattr(
        service,
        "read_analysis_in_session",
        lambda *a: (
            checked.append("analysis_bytes")
            or NS(
                result_sha256=outcome.result_sha256,
                measurement_sha256=outcome.measurement_sha256,
            )
        ),
    )
    return NS(
        session=session,
        owner=m.owner_id,
        workflow=root.id,
        stage=stage.id,
        run=run,
        receipt=receipt,
        analysis=analysis,
        version=version,
        admissions=admissions,
        checked=checked,
        source=source,
        evaluation=evaluation,
        evaluated=evaluated,
        boundary=boundary,
        by_model=by_model,
        original_one=original_one,
        reservation=reservation,
        settings=NS(storage_root="/unused"),
    )


@pytest.mark.parametrize(
    "change", [None, "wrong_plan", "wrong_factor", "orphan_unit", "held_charge"]
)
def test_complete_child_graph_not_same_owner_status_shortcut(completed_graph, change):
    t = completed_graph
    if change == "wrong_plan":
        t.analysis.plan_id = uuid4()
    elif change == "wrong_factor":
        t.evaluation.factor_version_id = uuid4()
    elif change == "orphan_unit":
        t.admissions.append(NS(id=uuid4(), role="native_evaluator"))
    elif change == "held_charge":
        t.reservation.state = "uncertain"
    if change:
        with pytest.raises(DomainError):
            service.read_rd_terminal_in_session(
                t.session, t.owner, t.workflow, t.stage, t.settings
            )
    else:
        actual = service.read_rd_terminal_in_session(
            t.session, t.owner, t.workflow, t.stage, t.settings
        )
        assert actual.child_run_id == t.run.id and actual.plan_id == t.receipt.plan_id
        assert (
            actual.factor_version_id == t.version.id and actual.source_id == t.source.id
        )
        assert t.checked == ["controller_bytes", "source_bytes", "analysis_bytes"]
        assert (
            actual.qualification
            == "completed_owned_child_not_result_release_or_next_launch"
        )


@pytest.mark.parametrize(
    "field",
    [
        "generation_start_id",
        "snapshot_id",
        "snapshot_manifest_sha256",
        "snapshot_input_sha256",
        "snapshot_output_sha256",
        "controller_launch_id",
        "boundary_id",
        "status",
    ],
)
def test_current_execution_outcome_binding_drift_is_rejected(completed_graph, field):
    t = completed_graph
    setattr(
        t.evaluated,
        field,
        "0" * 64 if "sha256" in field else "rejected" if field == "status" else uuid4(),
    )
    with pytest.raises(DomainError):
        service.read_rd_terminal_in_session(
            t.session, t.owner, t.workflow, t.stage, t.settings
        )
    assert "analysis_bytes" not in t.checked


@pytest.mark.parametrize(
    "field,value",
    [("logical_step", 1), ("boundary", "advanced"), ("next_logical_step", 1)],
)
def test_success_requires_actual_returned_generation_boundary(
    completed_graph, field, value
):
    t = completed_graph
    setattr(t.boundary, field, value)
    with pytest.raises(DomainError):
        service.read_rd_terminal_in_session(
            t.session, t.owner, t.workflow, t.stage, t.settings
        )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing",
        "legacy_only",
        "owner",
        "run",
        "attempt",
        "generation",
        "evaluation",
    ],
)
def test_actual_execution_outcome_query_not_legacy_table(
    completed_graph, monkeypatch, change
):
    """Real mapped SQLite query; no PostgreSQL FK/trigger or physical-read claim."""
    from sqlalchemy import (
        CheckConstraint,
        ForeignKeyConstraint,
        MetaData,
        create_engine,
    )
    from sqlalchemy.orm import Session

    from backend.app.execution_snapshot_models import FactorExecutionEvaluationOutcome
    from backend.app.rdagent_models import FactorEvaluationOutcome

    t = completed_graph
    engine = create_engine("sqlite://")
    table = FactorExecutionEvaluationOutcome.__table__.to_metadata(MetaData())
    for constraint in tuple(table.constraints):
        if isinstance(constraint, (CheckConstraint, ForeignKeyConstraint)):
            table.constraints.remove(constraint)
    table.create(engine)
    # Only this current producer table exists. A legacy-table lookup is an SQL
    # error, not a fixture-supplied result; absence never grants completion.
    row = t.evaluated
    row.feedback_object_id = uuid4()
    row.feedback_object_kind = "factor_generation_feedback"
    row.feedback_sha256 = "f" * 64
    columns = {
        "owner": "owner_id",
        "run": "run_id",
        "attempt": "attempt_id",
        "evaluation": "evaluation_id",
    }
    if change in columns:
        setattr(row, columns[change], uuid4())
    if change == "generation":
        row.generation += 1
    one = service._one
    try:
        with Session(engine, expire_on_commit=False) as database:
            if change == "legacy_only":
                legacy = FactorEvaluationOutcome.__table__.to_metadata(MetaData())
                for constraint in tuple(legacy.constraints):
                    if isinstance(constraint, (CheckConstraint, ForeignKeyConstraint)):
                        legacy.constraints.remove(constraint)
                legacy.create(engine)
                database.add(
                    FactorEvaluationOutcome(
                        **{
                            column.name: getattr(row, column.name)
                            for column in legacy.columns
                            if column.name != "created_at" and hasattr(row, column.name)
                        }
                    )
                )
                database.commit()
            elif change != "missing":
                database.add(row)
                database.commit()
                database.expunge_all()

            def query(session, model, **filters):
                if model in (FactorExecutionEvaluationOutcome, FactorEvaluationOutcome):
                    return t.original_one(database, model, **filters)
                return one(session, model, **filters)

            monkeypatch.setattr(service, "_one", query)
            if change:
                with pytest.raises(DomainError):
                    service.read_rd_terminal_in_session(
                        t.session, t.owner, t.workflow, t.stage, t.settings
                    )
            else:
                assert (
                    service.read_rd_terminal_in_session(
                        t.session, t.owner, t.workflow, t.stage, t.settings
                    ).child_run_id
                    == t.run.id
                )
    finally:
        engine.dispose()
