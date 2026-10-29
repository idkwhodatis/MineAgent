"""Fresh declaration shared by normal queued writer and future workflow admission."""

# ruff: noqa: F811
from types import SimpleNamespace as NS

import pytest
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401
from test_rdagent_provider_preflight_service import preflight_inputs  # noqa: F401
from test_rdagent_submission_service import queued_writer  # noqa: F401

from backend.app import rdagent_provider_declaration as provider
from backend.app import rdagent_submission_services as service
from backend.app.models import ResearchRun
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest


def test_real_retained_builder_writes_nothing_and_normal_writer_uses_exact_config(
    queued_writer,
):
    q = queued_writer
    request = q.body.request()
    checked = service.preflight(
        q.session, q.source.fence.owner_id, request, q.source.settings
    )
    config, source = service.resolve_research_declaration(
        q.session,
        q.source.fence.owner_id,
        request,
        q.source.settings,
        q.source.config.runtime,
        expected_input_sha256=checked.frozen_input_sha256,
    )
    assert (
        not q.session.rows and source.dataset.dataset_version_id == q.source.dataset.id
    )
    service._persist_queued(
        q.session,
        q.source.fence.owner_id,
        q.body,
        "builder-equivalence",
        q.source.settings,
        q.source.config.runtime,
        "sha256:" + "e" * 64,
    )
    run = next(row for row in q.session.rows if isinstance(row, ResearchRun))
    assert canonical_bytes(run.config) == canonical_bytes(config)
    assert run.request_sha256 == config.sha256


def test_changed_source_builder_cannot_allocate_any_rows(queued_writer):
    q = queued_writer
    with pytest.raises(DomainError, match="Retained source"):
        service.resolve_research_declaration(
            q.session,
            q.source.fence.owner_id,
            q.body.request(),
            q.source.settings,
            q.source.config.runtime,
            expected_input_sha256="f" * 64,
        )
    assert not q.session.rows


@pytest.mark.parametrize("case", ["accepted", "disabled", "drift"])
def test_provider_builder_uses_only_fresh_server_profile_and_no_queue(
    preflight_inputs, monkeypatch, case
):
    owner, request, snapshot, _base, session = preflight_inputs
    events = []
    profile = {"runtime": "server-owned"}
    bound = NS(
        can_submit=case != "disabled",
        runtime_profile_sha256=digest(profile) if case != "drift" else "f" * 64,
        frozen_input_sha256="a" * 64,
        model_binding=object(),
        steer_snapshot=snapshot,
    )
    monkeypatch.setattr(
        provider, "_try_neutral_lock", lambda s, k: events.append(("owner", k))
    )
    monkeypatch.setattr(provider, "provider_preflight", lambda *a: bound)
    profile = NS(runtime="server-owned", controller_profile=None)
    monkeypatch.setattr(provider, "resolve_provider_runtime", lambda settings: profile)
    monkeypatch.setattr(provider, "digest", lambda p: digest({"runtime": p.runtime}))

    def resolved(*args, **kwargs):
        assert (
            args[4] == "server-owned" and kwargs["model_binding"] is bound.model_binding
        )
        assert kwargs["steer_snapshot"] is snapshot
        events.append(("source", owner))
        return "actual-config", "actual-source"

    monkeypatch.setattr(provider, "resolve_research_declaration", resolved)
    if case == "accepted":
        result = provider.prepare_provider_declaration(
            session, owner, request, object()
        )
        assert result.config == "actual-config" and result.request == request
        assert events == [("owner", f"submit:{owner}"), ("source", owner)]
    else:
        with pytest.raises(DomainError):
            provider.prepare_provider_declaration(session, owner, request, object())
        assert events == [("owner", f"submit:{owner}")]


def test_new_child_insertion_uses_allocated_id_without_receipt_or_outbox(
    queued_writer, monkeypatch
):
    from test_rdagent_workflow_child_contracts import config as workflow_config

    from backend.app.factor_analysis_models import FactorAnalysisPlan
    from backend.app.models import OutboxEvent
    from backend.app.rdagent_submission_models import FactorResearchSubmission
    from packages.rdagent_adapter.steer import SteerSnapshot
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from packages.research_workflow.contracts import WorkflowInputSnapshot
    from packages.research_workflow.model_inputs import ModelStageInputSnapshot

    q = queued_writer
    owner = q.source.fence.owner_id
    request = q.body.request()
    checked = service.preflight(q.session, owner, request, q.source.settings)
    base, source = service.resolve_research_declaration(
        q.session,
        owner,
        request,
        q.source.settings,
        q.source.config.runtime,
        expected_input_sha256=checked.frozen_input_sha256,
    )
    value = workflow_config()
    selected = WorkflowInputSnapshot.model_validate(
        {
            **value.workflow_inputs.selection.model_dump(),
            "owner_id": owner,
        }
    )
    inputs = ModelStageInputSnapshot.model_validate(
        {
            **value.workflow_inputs.model_dump(),
            "selection": selected,
            "binding": {
                **value.workflow_inputs.binding.model_dump(),
                "owner_id": owner,
                "input_sha256": selected.sha256,
            },
        }
    )
    empty = SteerSnapshot(owner_id=owner)
    child = WorkflowFactorResearchConfig.model_validate(
        {
            **value.model_dump(),
            "input": base.input,
            "runtime": base.runtime,
            "task": base.task,
            "budget": base.budget,
            "workflow_inputs": inputs,
            "workflow_inputs_sha256": inputs.sha256,
            "steer_snapshot": empty,
            "steer_snapshot_sha256": empty.sha256,
        }
    )
    old_get = q.session.get
    monkeypatch.setattr(
        q.session,
        "get",
        lambda model, key: None if model is ResearchRun else old_get(model, key),
    )
    run, spec, plan = service._insert_declared_queued_graph(
        q.session,
        owner,
        request,
        "workflow-stage:allocated",
        q.source.settings,
        base.runtime,
        "sha256:" + "e" * 64,
        config=child,
        source=source,
        run_id=child.workflow_child_run_id,
    )
    assert run.id == child.workflow_child_run_id == spec.run_id == plan.run_id
    assert run.request_sha256 == child.sha256 and run.active_attempt_id is None
    assert isinstance(plan, FactorAnalysisPlan)
    assert not any(
        isinstance(row, (OutboxEvent, FactorResearchSubmission))
        for row in q.session.rows
    )
    count = len(q.session.rows)
    monkeypatch.setattr(q.session, "get", lambda model, key: run)
    with pytest.raises(DomainError, match="New workflow child differs"):
        service._insert_declared_queued_graph(
            q.session,
            owner,
            request,
            "workflow-stage:allocated",
            q.source.settings,
            base.runtime,
            "sha256:" + "e" * 64,
            config=child,
            source=source,
            run_id=child.workflow_child_run_id,
        )
    assert len(q.session.rows) == count
