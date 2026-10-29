"""Real retained-source graph writer with transaction/stage authority doubles.

No PostgreSQL or stage-CAS acceptance is claimed. Native stage guard tests cover
its separate boundary; these exercise the actual new child/receipt insertions.
"""

# ruff: noqa: F811
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401
from test_rdagent_submission_service import queued_writer  # noqa: F401
from test_rdagent_workflow_child_contracts import config as workflow_config

from backend.app import rdagent_submission_services as graph
from backend.app import rdagent_workflow_saved as saved
from backend.app import rdagent_workflow_submission as writer
from backend.app import research_input_resolution as inputs_reader
from backend.app import research_workflow_control as control
from backend.app import research_workflow_rd_stage as stage_guard
from backend.app.models import OutboxEvent, ResearchRun
from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSubmissionRequest,
    provider_commitment,
)
from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
from backend.app.research_workflow_models import (
    ResearchWorkflow,
    ResearchWorkflowRevision,
    ResearchWorkflowStage,
)
from backend.app.research_workflow_public_schemas import WorkflowRequest, WorkflowSubmit
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot
from packages.research_workflow.admission import CreateWorkflow
from packages.research_workflow.contracts import WorkflowInputSnapshot
from packages.research_workflow.plan import RDGuidancePlan
from packages.research_workflow.rd_stage import RDStageDefinition


@pytest.fixture
def actual_graph(queued_writer, monkeypatch):
    q = queued_writer
    (q.source.settings.storage_root / "factors").mkdir(exist_ok=True)
    owner = q.source.fence.owner_id
    original = q.body.request()
    checked = graph.preflight(q.session, owner, original, q.source.settings)
    base, source = graph.resolve_research_declaration(
        q.session,
        owner,
        original,
        q.source.settings,
        q.source.config.runtime,
        expected_input_sha256=checked.frozen_input_sha256,
    )
    sample = workflow_config()
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from packages.research_runtime.budget_contracts import BudgetVector

    minimum = provider_budget_floor(sample.model_binding)
    funded = BudgetVector(
        **{
            key: max(getattr(base.budget, key), getattr(minimum, key))
            for key in BudgetVector.model_fields
        }
    )
    empty = SteerSnapshot(owner_id=owner)
    config = ProviderFactorResearchConfig.model_validate(
        {
            **base.model_dump(mode="python"),
            "budget": funded,
            "schema_version": "mineagent.factor-research.v2",
            "model_profile": "configured.rdagent.v1",
            "model_binding": sample.model_binding,
            "steer_snapshot": empty,
            "steer_snapshot_sha256": empty.sha256,
        }
    )
    request = RDProviderSubmissionRequest.model_validate(
        {
            **original.model_dump(mode="python"),
            "budget": funded,
            "schema_version": "mineagent.rdagent-provider-submission.v1",
            "model_profile_id": config.model_binding.profile_id,
            "selected_steer_version_ids": (),
        }
    )
    runtime = RDProviderServerRuntimeProfile(
        runtime=config.runtime, qlib_installed_parent_image_id="sha256:" + "e" * 64
    )
    import sys

    q.source.settings.rdagent_provider_runtime_profile = runtime
    q.source.settings.rdagent_provider_python = sys.executable
    plan = RDGuidancePlan(
        owner_id=owner,
        maximum_iterations=1,
        research_template=config,
        objective=request.objective,
        source_policy=request.source_policy,
        qlib_installed_parent_image_id=runtime.qlib_installed_parent_image_id,
        analysis_dependency_lock_sha256={
            v.role: v.sha256 for v in config.runtime.dependency_locks
        }["analysis"],
        initial_parent_factor_version_id=None,
        budget_ceiling=config.budget,
    )
    workflow_id, stage_id, revision_id, child_id = (uuid4() for _ in range(4))
    selection = WorkflowInputSnapshot(
        owner_id=owner,
        workflow_id=workflow_id,
        revision_id=revision_id,
        refs=sample.workflow_inputs.selection.refs,
    )
    definition = RDStageDefinition(
        plan_sha256=plan.sha256,
        research_iteration=0,
        predecessor_checkpoint_id=None,
        parent_factor_version_id=None,
        parent_source_stage_id=None,
    )
    public = WorkflowSubmit(
        idempotency_key="workflow-public-key",
        preflight_sha256="a" * 64,
        request=WorkflowRequest(
            research=request,
            maximum_iterations=1,
            budget_ceiling=plan.budget_ceiling,
            refs=selection.refs,
        ),
    )
    creation = CreateWorkflow(
        idempotency_key=public.idempotency_key,
        plan=plan,
        refs=selection.refs,
        public_submission_canonical=canonical_bytes(public).decode(),
    )
    root = NS(
        id=workflow_id,
        owner_id=owner,
        creation_key=public.idempotency_key,
        creation_canonical=canonical_bytes(creation).decode(),
        creation_sha256=digest(creation),
        plan_canonical=canonical_bytes(plan).decode(),
        plan_sha256=plan.sha256,
        budget_canonical=canonical_bytes(plan.budget_ceiling).decode(),
        budget_sha256=digest(plan.budget_ceiling),
        current_stage_id=stage_id,
        current_input_revision_id=revision_id,
        status="running",
    )
    stage = NS(
        id=stage_id,
        owner_id=owner,
        workflow_id=workflow_id,
        input_revision_id=revision_id,
        child_run_id=child_id,
        kind="rd_factor_generation",
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=definition.sha256,
        allocation_canonical=canonical_bytes(config.budget).decode(),
        allocation_sha256=digest(config.budget),
    )
    revision = NS(
        id=revision_id,
        owner_id=owner,
        workflow_id=workflow_id,
        inputs_canonical=canonical_bytes(selection).decode(),
        inputs_sha256=selection.sha256,
    )
    context = NS(
        root=root, stage=stage, plan=plan, selection=selection, definition=definition
    )
    admission = object()

    def current(s, own, wid, sid, settings, *, admission):
        assert (s, own, wid, sid, settings) == (
            q.session,
            owner,
            workflow_id,
            stage_id,
            q.source.settings,
        )
        assert admission is token
        return context

    token = admission
    from contextlib import contextmanager

    from backend.app.factor_native_io import native_storage_guard

    @contextmanager
    def publication_guard_double(session, settings, *, admission):
        assert session is q.session and admission is token
        with native_storage_guard(settings.storage_root):
            yield

    monkeypatch.setattr(
        writer, "native_guard_from_publication", publication_guard_double
    )
    monkeypatch.setattr(stage_guard, "require_current_rd_stage", current)
    actual_inputs = sample.workflow_inputs.inputs
    monkeypatch.setattr(
        inputs_reader,
        "verify_model_input_versions",
        lambda s, own, refs, settings: actual_inputs,
    )
    preflight_sha = provider_commitment(
        owner,
        request,
        dataset_sha256=source.dataset.content_sha256,
        input_sha256=config.input.sha256,
        profile_sha256=digest(runtime),
        model_binding_sha256=config.model_binding.sha256,
        steer_snapshot_sha256=empty.sha256,
    )
    prepared = NS(
        request=request,
        config=config,
        runtime_profile=runtime,
        preflight=NS(parent=None, preflight_sha256=preflight_sha),
    )
    monkeypatch.setattr(writer, "prepare_provider_declaration", lambda *a: prepared)
    fixed = {
        ResearchWorkflow: [root],
        ResearchWorkflowStage: [stage],
        ResearchWorkflowRevision: [revision],
    }

    def scalar(query):
        if "count(" in str(query):
            return 0
        model = query.column_descriptions[0].get("entity")
        if model is None:
            return 0
        params = {k.rsplit("_", 1)[0]: v for k, v in query.compile().params.items()}
        rows = fixed.get(
            model, [row for row in q.session.rows if isinstance(row, model)]
        )
        return next(
            (
                row
                for row in rows
                if all(getattr(row, k, None) == v for k, v in params.items())
            ),
            None,
        )

    monkeypatch.setattr(q.session, "scalar", scalar)
    old_get = q.session.get

    def get(model, identity):
        found = next(
            (
                row
                for row in q.session.rows
                if isinstance(row, model) and row.id == identity
            ),
            None,
        )
        if found is not None or model is ResearchRun:
            return found
        return old_get(model, identity)

    monkeypatch.setattr(q.session, "get", get)
    monkeypatch.setattr(control, "require_schema", lambda session: None)
    return NS(
        q=q,
        owner=owner,
        root=root,
        stage=stage,
        prepared=prepared,
        context=context,
        admission=admission,
        fixed=fixed,
    )


def submit(value):
    receipt = writer.submit_workflow_child(
        value.q.session,
        value.owner,
        value.root.id,
        value.stage.id,
        value.q.source.settings,
        admission=value.admission,
    )
    # The database materializes SQL NULL; this session double has no DB flush.
    from sqlalchemy.sql.elements import Null

    for row in value.q.session.rows:
        if isinstance(row, ResearchRun) and isinstance(row.submission_binding, Null):
            row.submission_binding = None
    return receipt


def test_atomic_writer_creates_real_new_graph_and_immutable_reader_survives_head_edits(
    actual_graph, monkeypatch
):
    v = actual_graph
    receipt = submit(v)
    assert isinstance(receipt, FactorWorkflowSubmission)
    assert receipt.run_id == v.stage.child_run_id
    assert receipt.submission_origin == "workflow_stage"
    assert len([r for r in v.q.session.rows if isinstance(r, OutboxEvent)]) == 1
    assert (
        saved.verify_workflow_submission(
            v.q.session, v.owner, receipt.id, v.q.source.settings
        )
        is receipt
    )
    v.root.current_stage_id, v.root.current_input_revision_id, v.root.status = (
        uuid4(),
        uuid4(),
        "paused",
    )
    monkeypatch.setattr(
        writer,
        "prepare_provider_declaration",
        lambda *a: pytest.fail("not current provider/active selection"),
    )
    assert (
        saved.verify_workflow_submission(
            v.q.session, v.owner, receipt.id, v.q.source.settings
        )
        is receipt
    )


@pytest.mark.parametrize("change", ["profile", "analysis_lock", "existing_receipt"])
def test_new_child_rejects_before_second_graph(actual_graph, change):
    v = actual_graph
    if change == "existing_receipt":
        submit(v)
    elif change == "analysis_lock":
        v.context.plan = v.context.plan.model_copy(
            update={"analysis_dependency_lock_sha256": "f" * 64}
        )
    else:
        v.prepared.config = v.prepared.config.model_copy(
            update={"max_output_rows_per_cutoff": 9999}
        )
    count = len(v.q.session.rows)
    with pytest.raises(DomainError):
        submit(v)
    assert len(v.q.session.rows) == count


@pytest.mark.parametrize(
    "change",
    ["receipt_hash", "child", "selection", "request", "analysis", "model", "owner"],
)
def test_saved_child_cannot_rebind_actual_graph(actual_graph, change):
    v = actual_graph
    receipt = submit(v)
    owner = v.owner
    if change == "receipt_hash":
        receipt.config_sha256 = "f" * 64
    elif change == "child":
        v.stage.child_run_id = uuid4()
    elif change == "selection":
        receipt.selection_sha256 = "f" * 64
    elif change == "request":
        receipt.request_sha256 = "f" * 64
    elif change == "owner":
        owner = uuid4()
    elif change == "model":
        run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
        run.config = {**run.config, "model_profile": "configured.rdagent.v1"}
    else:
        from backend.app.factor_analysis_models import FactorAnalysisPlan

        plan = next(r for r in v.q.session.rows if isinstance(r, FactorAnalysisPlan))
        plan.objective_sha256 = "f" * 64
    with pytest.raises(DomainError):
        saved.verify_workflow_submission(
            v.q.session, owner, receipt.id, v.q.source.settings
        )


def test_delivery_metadata_does_not_claim_file_validation_or_consume_active_heads(
    actual_graph, monkeypatch
):
    value = actual_graph
    receipt = submit(value)
    monkeypatch.setattr(
        inputs_reader,
        "verify_model_input_versions",
        lambda *a: pytest.fail("delivery must not read files"),
    )
    monkeypatch.setattr(
        saved,
        "resolve_research_declaration",
        lambda *a, **kw: pytest.fail("delivery must not read source"),
    )
    assert (
        saved.verify_workflow_submission_metadata(
            value.q.session, value.owner, receipt.id
        )
        is receipt
    )
    with pytest.raises(BaseException, match="delivery must not read files"):
        saved.verify_workflow_submission(
            value.q.session, value.owner, receipt.id, value.q.source.settings
        )


@pytest.mark.parametrize("read_inputs", [False, True])
def test_actual_queue_seam_uses_distinct_immutable_workflow_receipt(
    actual_graph, monkeypatch, read_inputs
):
    from workers.research import rdagent_workflow_queue as queue

    v = actual_graph
    receipt = submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    event = next(r for r in v.q.session.rows if isinstance(r, OutboxEvent))
    monkeypatch.setattr(
        v.q.session, "scalars", lambda query: NS(all=lambda: ["029_research_workflow"])
    )
    monkeypatch.setattr(
        queue, "resolve_provider_runtime", lambda settings: v.prepared.runtime_profile
    )
    monkeypatch.setattr(
        queue,
        "resolve_provider_declaration",
        lambda *a: (object(), v.prepared.config.model_binding),
    )
    if not read_inputs:
        monkeypatch.setattr(
            queue,
            "verify_workflow_submission",
            lambda *a: pytest.fail("dispatch cannot claim file proof"),
        )
    config, found = queue.require_workflow_queue_binding(
        v.q.session, run, event, v.q.source.settings, read_inputs=read_inputs
    )
    assert found is receipt and config.workflow_child_run_id == run.id
    assert run.active_attempt_id is None and run.status == "queued"
    run.config = {**run.config, "workflow_child_run_id": str(uuid4())}
    with pytest.raises(ValueError):
        queue.require_workflow_queue_binding(
            v.q.session, run, event, v.q.source.settings, read_inputs=read_inputs
        )


def test_old_schema_cannot_dispatch_workflow_child(actual_graph, monkeypatch):
    from workers.research import rdagent_workflow_queue as queue

    v = actual_graph
    submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    event = next(r for r in v.q.session.rows if isinstance(r, OutboxEvent))
    monkeypatch.setattr(
        v.q.session,
        "scalars",
        lambda query: NS(all=lambda: ["028_oe_structured_search"]),
    )
    monkeypatch.setattr(
        queue,
        "resolve_provider_runtime",
        lambda *a: pytest.fail("old schema cannot inspect operator"),
    )
    with pytest.raises(DomainError, match="schema unavailable"):
        queue.require_workflow_queue_binding(
            v.q.session, run, event, v.q.source.settings, read_inputs=False
        )


def test_controller_bootstrap_is_derived_from_same_receipt_then_rereads_inputs(
    actual_graph, monkeypatch
):
    from backend.app import rdagent_workflow_controller as controller
    from backend.app.rdagent_models import FactorResearchSpec
    from packages.rdagent_adapter.workflow_identity import (
        require_workflow_source_identity,
    )
    from packages.research_runtime.fences import AttemptFence

    v = actual_graph
    receipt = submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    spec = next(r for r in v.q.session.rows if isinstance(r, FactorResearchSpec))
    fence = AttemptFence(v.owner, run.id, uuid4(), 1, uuid4())
    binding = NS(
        id=uuid4(),
        owner_id=v.owner,
        run_id=run.id,
        attempt_id=fence.attempt_id,
        generation=1,
        spec_id=spec.id,
    )
    monkeypatch.setattr(
        v.q.session, "scalars", lambda query: NS(all=lambda: ["029_research_workflow"])
    )
    monkeypatch.setattr(
        controller,
        "require_neutral_transaction",
        lambda session, current, **kw: (
            None if current == fence else pytest.fail("scope")
        ),
    )
    bootstrap = controller.workflow_bootstrap_if_declared(
        v.q.session,
        fence,
        binding=binding,
        spec=spec,
        config=require_workflow_source_identity(run),
    )
    assert (
        bootstrap.run_id == receipt.run_id
        and bootstrap.config.workflow_inputs.binding.stage_id == v.stage.id
    )
    controller.reread_workflow_bootstrap_inputs(
        v.q.session, fence, bootstrap, v.q.source.settings
    )
    binding.run_id = uuid4()
    with pytest.raises(DomainError):
        controller.workflow_bootstrap_if_declared(
            v.q.session,
            fence,
            binding=binding,
            spec=spec,
            config=require_workflow_source_identity(run),
        )


def test_historical_workflow_saved_reader_requires_real_native_guard(actual_graph):
    from backend.app.factor_native_io import NativeStorageError, native_storage_guard

    v = actual_graph
    receipt = submit(v)
    with pytest.raises(NativeStorageError):
        saved.verify_workflow_submission_under_native_guard(
            v.q.session, v.owner, receipt.id, v.q.source.settings
        )
    (v.q.source.settings.storage_root / "factors").mkdir(exist_ok=True)
    with native_storage_guard(v.q.source.settings.storage_root):
        assert (
            saved.verify_workflow_submission_under_native_guard(
                v.q.session, v.owner, receipt.id, v.q.source.settings
            )
            is receipt
        )


def test_workflow_metadata_list_detail_and_history_use_exact_schema(
    actual_graph, monkeypatch
):
    from datetime import UTC, datetime

    from backend.app import engine_run_views as views

    v = actual_graph
    receipt = submit(v)
    run = v.q.session.get(ResearchRun, receipt.run_id)
    run.created_at = run.updated_at = datetime.now(UTC)
    run.error = None
    revision = ["029_research_workflow"]

    def scalars(query):
        return NS(all=lambda: revision if "alembic_version" in str(query) else [run])

    monkeypatch.setattr(v.q.session, "scalars", scalars)
    result = views.list_history_runs(v.q.session, v.owner, 0, 10)
    assert result["data"][0].config_schema == "mineagent.factor-research.v3"
    detail = views.detail(run, session=v.q.session)
    assert detail.config_sha256 == receipt.config_sha256
    monkeypatch.setattr(views, "owned_run", lambda session, run_id, owner: run)
    assert views.owned_engine_run(v.q.session, run.id, v.owner) is run
    monkeypatch.setattr(
        v.q.session, "execute", lambda query: NS(all=list), raising=False
    )
    assert views.history(v.q.session, run, 0, 10) == {"data": [], "count": 0}
    with pytest.raises(DomainError):
        views.detail(run)
    revision[:] = ["028_oe_structured_search"]
    with pytest.raises(DomainError):
        views.detail(run, session=v.q.session)


def test_workflow_input_origin_uses_actual_immutable_receipt_not_manual_approval(
    actual_graph, monkeypatch
):
    from datetime import UTC, datetime

    from backend.app import engine_input_routes as routes

    v = actual_graph
    receipt = submit(v)
    run = v.q.session.get(ResearchRun, receipt.run_id)
    receipt.created_at = datetime.now(UTC)
    monkeypatch.setattr(
        v.q.session, "scalars", lambda query: NS(all=lambda: ["029_research_workflow"])
    )
    v.root.current_stage_id = uuid4()
    v.root.current_input_revision_id = uuid4()
    v.root.status = "paused"
    item = routes.workflow_submission_item(
        v.q.session, run, receipt, v.q.source.settings
    )
    assert item.origin == "workflow_stage"
    assert not item.default_pinned and item.submission is None
    assert item.provenance == "workflow_stage_submission_receipt"
    assert item.workflow_inputs_sha256 == receipt.resolved_inputs_sha256
    assert item.workflow_inputs.sha256 == item.content_sha256
    assert item.steer_evidence == "accepted_input_snapshot_not_execution_or_compliance"
    receipt.config_sha256 = "f" * 64
    with pytest.raises(DomainError):
        routes.workflow_submission_item(v.q.session, run, receipt, v.q.source.settings)


@pytest.mark.parametrize("version", [1, 2])
def test_old_metadata_config_parser_remains_unchanged(actual_graph, version):
    from backend.app.engine_run_views import rd_config
    from packages.rdagent_adapter.persisted_identity import require_source_identity

    v = actual_graph
    config = v.q.source.config if version == 1 else v.prepared.config
    # Actual persisted identity validator remains the oracle for historical v1/v2.
    run = NS(
        config=config.model_dump(mode="json"),
        owner_id=v.owner,
        id=uuid4(),
        engine="rdagent",
        purpose="factor_research",
        dataset_version_id=config.input.dataset.dataset_version_id,
        request_sha256=config.sha256,
        research_image_digest=None,
        **{
            key: None
            for key in (
                "strategy_version_id",
                "experiment_id",
                "split_version_id",
                "final_selection_id",
                "submission_binding",
                "portable_policy_image_digest",
                "latest_checkpoint_id",
                "resume_checkpoint_id",
            )
        },
    )
    assert rd_config(run) == require_source_identity(run)


def test_workflow_inputs_route_reads_real_receipt_and_rejects_missing_or_alias(
    actual_graph, monkeypatch
):
    from datetime import UTC, datetime

    from backend.app import engine_input_routes as routes
    from backend.app.rdagent_submission_models import FactorResearchSubmission

    v = actual_graph
    receipt = submit(v)
    run = v.q.session.get(ResearchRun, receipt.run_id)
    receipt.created_at = run.created_at = datetime.now(UTC)
    monkeypatch.setattr(
        routes, "owned_engine_run", lambda session, identity, owner: run
    )
    # Shared 028 installation is assembled separately; do not claim a PG capability here.
    monkeypatch.setattr(
        routes,
        "capabilities_for",
        lambda revision: NS(
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            rd_seed_schema="mineagent.rd-seed-restore-closure.v1",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v8",
        ),
    )
    monkeypatch.setattr(
        v.q.session,
        "scalars",
        lambda query: NS(
            all=lambda: (
                ["029_research_workflow"] if "alembic_version" in str(query) else []
            )
        ),
    )
    from contextlib import nullcontext

    from backend.app.core import maintenance

    monkeypatch.setattr(maintenance, "shared_lifecycle", lambda *a: nullcontext())
    request = NS(app=NS(state=NS(settings=v.q.source.settings, engine=object())))
    result = routes.read_inputs(run.id, NS(id=v.owner), v.q.session, request, offset=0)
    assert len(result.items) == 1
    assert result.items[0].origin == "workflow_stage"
    assert result.items[0].content_sha256 == receipt.resolved_inputs_sha256
    v.q.session.rows.remove(receipt)
    with pytest.raises(DomainError):
        routes.read_inputs(run.id, NS(id=v.owner), v.q.session, request, offset=0)
    v.q.session.rows.append(receipt)
    v.q.session.rows.append(
        FactorResearchSubmission(id=uuid4(), owner_id=v.owner, run_id=run.id)
    )
    with pytest.raises(DomainError):
        routes.read_inputs(run.id, NS(id=v.owner), v.q.session, request, offset=0)


def test_normal_dispatch_and_real_attempt_claim_use_workflow_receipt(
    actual_graph, monkeypatch
):
    from backend.app.models import ExecutionAttempt
    from workers.research import rdagent_queue as queue
    from workers.research import rdagent_workflow_queue as workflow

    v = actual_graph
    receipt = submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    event = next(r for r in v.q.session.rows if isinstance(r, OutboxEvent))
    monkeypatch.setattr(
        v.q.session, "scalars", lambda q: NS(all=lambda: ["029_research_workflow"])
    )
    original_scalar = v.q.session.scalar

    def scalar(query):
        column = query.column_descriptions[0]
        if column.get("entity") is ResearchRun and column.get("name") == "owner_id":
            return v.owner
        if column.get("entity") is ExecutionAttempt:
            return next(
                (r for r in v.q.session.rows if isinstance(r, ExecutionAttempt)), None
            )
        return original_scalar(query)

    monkeypatch.setattr(v.q.session, "scalar", scalar)
    monkeypatch.setattr(queue, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        workflow, "resolve_provider_runtime", lambda s: v.prepared.runtime_profile
    )
    monkeypatch.setattr(
        workflow,
        "resolve_provider_declaration",
        lambda *a: (None, v.prepared.config.model_binding),
    )
    settings = NS(**vars(v.q.source.settings), lease_seconds=60)
    queue.require_dispatch_binding(v.q.session, run, event, settings)
    assert run.active_attempt_id is None
    fence = queue._claim(
        v.q.session, run.id, 1, "workflow-test-worker", settings, None, None
    )
    attempt = next(r for r in v.q.session.rows if isinstance(r, ExecutionAttempt))
    assert fence.run_id == receipt.run_id and fence.attempt_id == attempt.id
    assert run.status == "running" and run.active_attempt_id == attempt.id
    assert (
        queue._claim(
            v.q.session, run.id, 1, "workflow-test-worker", settings, None, None
        )
        is None
    )


def test_workflow_spec_and_fence_keep_legacy_closed_and_expiry_fenced(
    actual_graph, monkeypatch
):
    from datetime import timedelta

    from backend.app import rdagent_services as services
    from backend.app.models import utcnow
    from backend.app.research_lifecycle import _check_neutral_fence
    from packages.research_runtime.fences import AttemptFence

    v = actual_graph
    submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    fence = AttemptFence(v.owner, run.id, uuid4(), 1, uuid4())
    run.active_attempt_id = fence.attempt_id
    run.status = "running"
    attempt = NS(
        id=fence.attempt_id,
        run_id=run.id,
        generation=1,
        lease_token=fence.lease_token,
        lease_expires_at=utcnow() + timedelta(seconds=60),
        status="running",
        finished_at=None,
    )
    with pytest.raises(DomainError):
        _check_neutral_fence(run, attempt, fence)
    _check_neutral_fence(run, attempt, fence, workflow=True)
    monkeypatch.setattr(
        v.q.session, "scalars", lambda q: NS(all=lambda: ["029_research_workflow"])
    )
    monkeypatch.setattr(
        services, "require_neutral_transaction", lambda *a: (run, attempt)
    )
    spec, config = services.require_research_spec(v.q.session, fence)
    assert (
        config.workflow_child_run_id == run.id and spec.config_sha256 == config.sha256
    )
    attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(DomainError):
        _check_neutral_fence(run, attempt, fence, workflow=True)


def test_actual_first_workflow_request_records_exact_inclusion_not_later_calls(
    actual_graph, monkeypatch
):
    from backend.app import rdagent_workflow_prompt_guard as guard
    from backend.app.research_workflow_models import ResearchWorkflowModelConsumption
    from packages.rdagent_adapter.workflow_identity import (
        require_workflow_source_identity,
    )
    from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
    from packages.rdagent_provider.codec import request_sha256
    from packages.rdagent_provider.contracts import (
        ProviderMessage,
        RDProviderChatRequest,
    )
    from packages.research_runtime.fences import AttemptFence

    v = actual_graph
    receipt = submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    config = require_workflow_source_identity(run)
    fence = AttemptFence(v.owner, run.id, uuid4(), 1, uuid4())
    monkeypatch.setattr(
        v.q.session, "scalars", lambda q: NS(all=lambda: ["029_research_workflow"])
    )
    monkeypatch.setattr(guard, "require_neutral_transaction", lambda *a, **kw: None)
    system, user = first_workflow_messages(config)
    request = RDProviderChatRequest(
        model_profile_id=config.model_binding.profile_id,
        model_binding_sha256=config.model_binding.sha256,
        model=config.model_binding.chat_model,
        response_format=None,
        max_output_tokens=config.model_binding.max_output_tokens,
        messages=(
            ProviderMessage(role="system", content=system),
            ProviderMessage(role="user", content=user),
        ),
    )
    call = NS(id=uuid4(), phase="generation", ordinal=0)
    operation = NS(
        id=uuid4(),
        protocol=request.schema_version,
        request_sha256=request_sha256(request.schema_version, request),
    )
    member = NS(
        operation_id=operation.id,
        call_id=call.id,
        request_ordinal=0,
        relation="initial",
    )
    transport = NS(id=uuid4(), operation_id=operation.id, ordinal=1)
    args = {
        "call": call,
        "member": member,
        "operation": operation,
        "transport": transport,
        "request": request,
        "config": config,
        "settings": v.q.source.settings,
    }
    with pytest.raises(ValueError, match="lacks original"):
        guard.bind_workflow_consumption(v.q.session, fence, **args, replay=True)
    row = guard.bind_workflow_consumption(v.q.session, fence, **args)
    assert isinstance(row, ResearchWorkflowModelConsumption)
    assert (row.stage_id, row.run_id, row.input_sha256, row.request_sha256) == (
        receipt.stage_id,
        run.id,
        config.workflow_inputs_sha256,
        operation.request_sha256,
    )
    assert (
        guard.bind_workflow_consumption(v.q.session, fence, **args, replay=True) is row
    )
    # Current root/head edits do not mutate the actual frozen first request.
    v.root.status = "pause_requested"
    v.root.current_input_revision_id = uuid4()
    assert (
        guard.bind_workflow_consumption(v.q.session, fence, **args, replay=True) is row
    )
    changed = request.model_copy(
        update={
            "messages": (ProviderMessage(role="user", content="omit frozen guidance"),)
        }
    )
    with pytest.raises(ValueError, match="combined inputs"):
        guard.require_first_workflow_prompt(
            v.q.session, fence, call, changed, config, v.q.source.settings
        )
    call.phase = "knowledge_update"
    assert guard.bind_workflow_consumption(v.q.session, fence, **args) is None
    assert (
        len(
            [
                r
                for r in v.q.session.rows
                if isinstance(r, ResearchWorkflowModelConsumption)
            ]
        )
        == 1
    )


def test_normal_cancel_uses_immutable_workflow_boundary_not_current_selection(
    actual_graph, monkeypatch
):
    from backend.app import rdagent_control_services as control_services
    from backend.app.rdagent_workflow_boundary import workflow_boundary_id

    v = actual_graph
    receipt = submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    monkeypatch.setattr(
        v.q.session, "scalars", lambda q: NS(all=lambda: ["029_research_workflow"])
    )
    monkeypatch.setattr(control_services, "_try_neutral_lock", lambda *a: None)
    events = []
    monkeypatch.setattr(
        control_services, "append_event", lambda *a, **kw: events.append((a, kw))
    )
    v.root.current_input_revision_id = uuid4()
    v.root.status = "pause_requested"
    assert (
        workflow_boundary_id(v.q.session, run, ["029_research_workflow"]) == receipt.id
    )
    assert workflow_boundary_id(v.q.session, run, ["028_oe_structured_search"]) is None
    assert control_services.controls_for(v.q.session, run).cancel
    result = control_services.request_cancel(v.q.session, v.owner, run.id)
    assert result.status == "cancelled" and run.status == "cancelled"
    assert run.active_attempt_id is None and len(events) == 1


@pytest.mark.parametrize("family", ["fresh", "seeded"])
def test_workflow_control_never_uses_another_submission_origin(
    actual_graph, monkeypatch, family
):
    from backend.app import rdagent_control_services as controls
    from backend.app.rdagent_seed_models import FactorSeededSubmission
    from backend.app.rdagent_submission_models import FactorResearchSubmission

    v = actual_graph
    submit(v)
    run = next(r for r in v.q.session.rows if isinstance(r, ResearchRun))
    cls = FactorResearchSubmission if family == "fresh" else FactorSeededSubmission
    v.q.session.rows.append(cls(id=uuid4(), owner_id=v.owner, run_id=run.id))
    monkeypatch.setattr(
        v.q.session, "scalars", lambda q: NS(all=lambda: ["029_research_workflow"])
    )
    monkeypatch.setattr(controls, "_try_neutral_lock", lambda *a: None)
    with pytest.raises(ValueError, match="distinct submission origin"):
        controls.request_cancel(v.q.session, v.owner, run.id)
    assert run.status == "queued" and run.active_attempt_id is None


def test_effective_later_request_changes_only_explicit_actual_parent_declaration(
    actual_graph,
    monkeypatch,
):
    from backend.app.rdagent_provider_submission_contracts import RDProviderSeedRequest
    from packages.research_runtime.budget_contracts import BudgetVector

    v = actual_graph
    original = writer._original_research_request(v.root, v.context.plan)
    plan = RDGuidancePlan.model_validate(
        {
            **v.context.plan.model_dump(mode="python"),
            "maximum_iterations": 2,
            "budget_ceiling": BudgetVector(
                **{
                    key: getattr(v.context.plan.research_template.budget, key) * 2
                    + (65536 if key == "storage_bytes" else 0)
                    for key in BudgetVector.model_fields
                }
            ).add(BudgetVector(storage_bytes=65536)),
        }
    )
    definition = RDStageDefinition(
        plan_sha256=plan.sha256,
        research_iteration=1,
        predecessor_checkpoint_id=uuid4(),
        parent_factor_version_id=uuid4(),
        parent_source_stage_id=uuid4(),
    )
    effective = writer.stage_research_request(original, plan, definition)
    assert type(effective) is RDProviderSeedRequest
    assert effective.parent_factor_version_id == definition.parent_factor_version_id
    assert effective.research_declaration() == original.research_declaration()
    assert effective.model_profile_id == original.model_profile_id
    assert effective.selected_steer_version_ids == ()
    assert original.schema_version == "mineagent.rdagent-provider-submission.v1"
    # Pure derivation is not permission: writer requires the real predecessor
    # verifier, which rejects absent terminal/release records.
    from backend.app import research_workflow_rd_predecessor as predecessor

    def absent(*args):
        raise DomainError(
            "workflow_predecessor_changed", "Absent actual predecessor", 409
        )

    monkeypatch.setattr(predecessor, "verify_rd_predecessor", absent)
    v.context.definition = definition
    with pytest.raises(DomainError, match="Absent actual predecessor"):
        submit(v)


@pytest.mark.parametrize("change", ["plan", "iterations", "initial_parent", "steer"])
def test_effective_request_rejects_unbound_stage_or_hidden_input(actual_graph, change):
    v = actual_graph
    original = writer._original_research_request(v.root, v.context.plan)
    definition = v.context.definition
    if change == "plan":
        definition = definition.model_copy(update={"plan_sha256": "f" * 64})
    elif change == "iterations":
        definition = RDStageDefinition(
            plan_sha256=v.context.plan.sha256,
            research_iteration=1,
            predecessor_checkpoint_id=uuid4(),
            parent_factor_version_id=uuid4(),
            parent_source_stage_id=uuid4(),
        )
    elif change == "initial_parent":
        definition = definition.model_copy(update={"parent_factor_version_id": uuid4()})
    else:
        original = original.model_copy(
            update={"selected_steer_version_ids": (uuid4(),)}
        )
    with pytest.raises(DomainError):
        writer.stage_research_request(original, v.context.plan, definition)


@pytest.mark.parametrize("drift", [None, "source_hash", "stage"])
def test_later_writer_and_saved_read_bind_actual_seed_bytes_and_required_predecessor(
    actual_graph, monkeypatch, drift
):
    """Real child graph/retained seed; predecessor authority is an explicit double."""
    from backend.app import research_workflow_rd_predecessor as predecessor_api
    from backend.app.factor_models import Factor, FactorObject, FactorVersion
    from backend.app.factor_native_io import (
        native_storage_guard,
        require_native_storage_guard,
    )
    from backend.app.rdagent_seed_services import resolve_seed_parent_under_native_guard
    from packages.factors.contracts import FactorDefinition, InputField, source_sha256
    from packages.research_runtime.budget_contracts import BudgetVector

    v = actual_graph
    original = writer._original_research_request(v.root, v.context.plan)
    plan = RDGuidancePlan.model_validate(
        {
            **v.context.plan.model_dump(mode="python"),
            "maximum_iterations": 2,
            "budget_ceiling": BudgetVector(
                **{
                    key: getattr(v.prepared.config.budget, key) * 2
                    + (65536 if key == "storage_bytes" else 0)
                    for key in BudgetVector.model_fields
                }
            ),
        }
    )
    source = "print('actual retained predecessor source')\n"
    sha = source_sha256(source)
    factor_id, version_id, object_id = uuid4(), uuid4(), uuid4()
    definition = FactorDefinition(
        code_sha256=sha,
        factor_kind="time_series",
        input_fields=(
            InputField(name="close", unit="USD", price_semantics="unadjusted"),
        ),
        lookback_sessions=2,
        output_unit="return",
    )
    key = f"factors/{v.owner}/objects/{sha}.py"
    path = v.q.source.settings.storage_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    path.chmod(0o444)
    v.fixed[Factor] = [NS(id=factor_id, owner_id=v.owner)]
    v.fixed[FactorVersion] = [
        NS(
            id=version_id,
            owner_id=v.owner,
            factor_id=factor_id,
            ordinal=1,
            code_object_id=object_id,
            code_sha256=sha,
            code=source,
            definition=definition.model_dump(mode="json"),
            definition_sha256=definition.sha256,
            source_provenance={"schema_version": "retained-test"},
        )
    ]
    v.fixed[FactorObject] = [
        NS(
            id=object_id,
            owner_id=v.owner,
            object_kind="code",
            media_type="text/plain",
            sha256=sha,
            size_bytes=len(source.encode()),
            storage_key=key,
        )
    ]
    stage_definition = RDStageDefinition(
        plan_sha256=plan.sha256,
        research_iteration=1,
        parent_factor_version_id=version_id,
        parent_source_stage_id=uuid4(),
        predecessor_checkpoint_id=uuid4(),
    )
    v.context.plan, v.context.definition = plan, stage_definition
    v.stage.definition_canonical = canonical_bytes(stage_definition).decode()
    v.stage.definition_sha256 = stage_definition.sha256
    creation = CreateWorkflow.model_validate_json(v.root.creation_canonical)
    public = WorkflowSubmit.model_validate_json(creation.public_submission_canonical)
    public = public.model_copy(
        update={
            "request": public.request.model_copy(
                update={"maximum_iterations": 2, "budget_ceiling": plan.budget_ceiling}
            )
        }
    )
    creation = creation.model_copy(
        update={
            "plan": plan,
            "public_submission_canonical": canonical_bytes(public).decode(),
        }
    )
    v.root.creation_canonical, v.root.creation_sha256 = (
        canonical_bytes(creation).decode(),
        digest(creation),
    )
    v.root.plan_canonical, v.root.plan_sha256 = (
        canonical_bytes(plan).decode(),
        plan.sha256,
    )
    v.root.budget_canonical, v.root.budget_sha256 = (
        canonical_bytes(plan.budget_ceiling).decode(),
        digest(plan.budget_ceiling),
    )
    effective = writer.stage_research_request(original, plan, stage_definition)
    with native_storage_guard(v.q.source.settings.storage_root):
        seed = resolve_seed_parent_under_native_guard(
            v.q.session, v.owner, version_id, v.q.source.settings
        )
    v.prepared.request = effective
    v.prepared.preflight.parent = seed.reference
    v.prepared.preflight.preflight_sha256 = provider_commitment(
        v.owner,
        effective,
        dataset_sha256=v.prepared.config.input.dataset.content_sha256,
        input_sha256=v.prepared.config.input.sha256,
        profile_sha256=digest(v.prepared.runtime_profile),
        model_binding_sha256=v.prepared.config.model_binding.sha256,
        steer_snapshot_sha256=v.prepared.config.steer_snapshot.sha256,
        parent_binding_sha256=seed.reference.sha256,
    )
    proof = NS(
        definition=stage_definition,
        parent_stage_id=uuid4()
        if drift == "stage"
        else stage_definition.parent_source_stage_id,
        feedback_checkpoint_id=stage_definition.predecessor_checkpoint_id,
        checkpoint=NS(
            factor_version_id=version_id,
            source_sha256="f" * 64 if drift == "source_hash" else sha,
        ),
    )
    calls = []

    def full(s, own, workflow, stage, settings):
        assert (s, own, workflow, stage) == (
            v.q.session,
            v.owner,
            v.root.id,
            v.stage.id,
        )
        require_native_storage_guard(settings.storage_root)
        calls.append("full")
        return proof

    monkeypatch.setattr(predecessor_api, "verify_rd_predecessor", full)
    monkeypatch.setattr(
        predecessor_api, "verify_rd_predecessor_metadata", lambda *a: proof
    )
    if drift:
        with pytest.raises(DomainError):
            submit(v)
        assert not any(isinstance(row, ResearchRun) for row in v.q.session.rows)
        return
    receipt = submit(v)
    assert receipt.request_sha256 == effective.sha256 != original.sha256
    run = v.q.session.get(ResearchRun, receipt.run_id)
    assert run.config["workflow_parent"]["code_sha256"] == sha
    assert (
        saved.verify_workflow_submission_metadata(v.q.session, v.owner, receipt.id)
        is receipt
    )
    assert calls == ["full"]
    assert (
        saved.verify_workflow_submission(
            v.q.session, v.owner, receipt.id, v.q.source.settings
        )
        is receipt
    )
    assert calls == ["full", "full"]
    path.chmod(0o644)
    path.write_text("changed bytes")
    path.chmod(0o444)
    with pytest.raises(DomainError):
        saved.verify_workflow_submission(
            v.q.session, v.owner, receipt.id, v.q.source.settings
        )


@pytest.mark.parametrize(
    "missing", ["retained_dataset", "released_feedback", "incomplete_restore"]
)
def test_public_workflow_input_body_requires_fresh_source_proof(
    actual_graph, monkeypatch, missing
):
    from datetime import UTC, datetime

    from backend.app import engine_input_routes as routes
    from backend.app.factor_native_io import require_native_storage_guard

    v = actual_graph
    receipt = submit(v)
    run = v.q.session.get(ResearchRun, receipt.run_id)
    receipt.created_at = datetime.now(UTC)
    monkeypatch.setattr(
        v.q.session, "scalars", lambda query: NS(all=lambda: ["029_research_workflow"])
    )
    # Queue delivery can still verify immutable relational metadata; that is not
    # authorization to disclose the cached combined text at a public endpoint.
    assert (
        saved.verify_workflow_submission_metadata(v.q.session, v.owner, receipt.id)
        is receipt
    )
    if missing == "incomplete_restore":
        (v.q.source.settings.storage_root / ".restore-incomplete").touch()
    elif missing == "retained_dataset":
        (v.q.source.directory / "manifest.json").unlink()
    else:

        def unavailable(session, owner, refs, settings):
            require_native_storage_guard(settings.storage_root)
            raise DomainError(
                "feedback_source_reader_unavailable", "No released source", 409
            )

        monkeypatch.setattr(inputs_reader, "verify_model_input_versions", unavailable)
    assert (
        saved.verify_workflow_submission_metadata(v.q.session, v.owner, receipt.id)
        is receipt
    )
    with pytest.raises(DomainError):
        routes.workflow_submission_item(v.q.session, run, receipt, v.q.source.settings)
