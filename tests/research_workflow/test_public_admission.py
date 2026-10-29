"""Public/server boundary doubles; no workflow registration or runtime claim."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_public as service
from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSubmissionRequest,
)
from backend.app.rdagent_server_profile import ROLE_LOCKS
from backend.app.research_workflow_public_schemas import WorkflowRequest, WorkflowSubmit
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.admission import CreateWorkflow
from tests.research_workflow.test_plan import plan


@pytest.fixture
def declaration(monkeypatch):
    p = plan()
    template = p.research_template
    runtime = template.runtime.model_dump(mode="json")
    runtime["dependency_locks"] = [
        {"role": role, "sha256": sha} for role, sha in sorted(ROLE_LOCKS.items())
    ]
    profile = RDProviderServerRuntimeProfile(
        runtime=runtime, qlib_installed_parent_image_id=p.qlib_installed_parent_image_id
    )
    template = template.model_copy(update={"runtime": profile.runtime})
    frozen = template.input
    research = RDProviderSubmissionRequest(
        dataset_version_id=frozen.dataset.dataset_version_id,
        window=frozen.window,
        input_fields=tuple(v.name for v in frozen.input_fields),
        lookback_sessions=frozen.lookback_sessions,
        lag_sessions=frozen.lag_sessions,
        task=template.task,
        objective=p.objective,
        budget=template.budget,
        max_iterations=1,
        source_policy=p.source_policy,
        model_profile_id="offline",
    )
    request = WorkflowRequest(
        research=research, maximum_iterations=2, budget_ceiling=p.budget_ceiling
    )
    events = []

    def prepare(*args):
        assert args[1] == p.owner_id and args[2] == request.research
        events.append("server_declaration")
        return NS(
            config=template,
            runtime_profile=profile,
            preflight=NS(preflight_sha256="a" * 64),
        )

    monkeypatch.setattr(service, "require_schema", lambda *a: None)
    monkeypatch.setattr(
        service, "_try_neutral_lock", lambda *a: events.append("owner_lock")
    )
    monkeypatch.setattr(service, "prepare_provider_declaration", prepare)
    monkeypatch.setattr(service, "resolve_model_input_versions", lambda *a: ())
    session = NS(scalar=lambda *a: None)
    return NS(**locals())


def test_server_template_and_runtime_not_public_knobs(declaration):
    f = declaration
    prepared = service.prepare_workflow(f.session, f.p.owner_id, f.request, None)
    assert prepared.plan.owner_id == f.p.owner_id
    assert prepared.plan.research_template.runtime == f.profile.runtime
    assert prepared.plan.analysis_dependency_lock_sha256 == ROLE_LOCKS["analysis"]
    assert prepared.plan.allow_workflow_development_feedback_release is False
    assert prepared.preflight.request_sha256 == f.request.sha256
    assert (
        prepared.preflight.qualification
        == "fresh_declaration_not_stage_execution_or_release"
    )


@pytest.mark.parametrize("key", ["owner_id", "runtime", "plan", "engine_image_id"])
def test_public_body_rejects_internal_launch_fields(declaration, key):
    value = declaration.request.model_dump(mode="json")
    value[key] = str(uuid4())
    with pytest.raises(ValueError):
        WorkflowRequest.model_validate(value)


def test_hidden_guidance_in_template_is_rejected(declaration):
    value = declaration.request.model_dump(mode="json")
    value["research"]["selected_steer_version_ids"] = [str(uuid4())]
    with pytest.raises(ValueError, match="ordered workflow refs"):
        WorkflowRequest.model_validate(value)


def test_submit_compares_fresh_preflight_and_retains_exact_original_body(
    declaration, monkeypatch
):
    f = declaration
    prepared = service.prepare_workflow(f.session, f.p.owner_id, f.request, None)
    body = WorkflowSubmit(
        idempotency_key="create-1",
        request=f.request,
        preflight_sha256=prepared.preflight.preflight_sha256,
    )
    retained = []
    monkeypatch.setattr(
        service,
        "create_workflow",
        lambda session, owner, value, settings: retained.append(value) or "created",
    )
    assert service.submit_workflow(f.session, f.p.owner_id, body, None) == "created"
    assert retained[0].public_submission_canonical == canonical_bytes(body).decode()
    assert retained[0].plan == prepared.plan


def test_changed_preflight_never_writes_workflow(declaration, monkeypatch):
    f = declaration
    body = WorkflowSubmit(
        idempotency_key="create-1", request=f.request, preflight_sha256="0" * 64
    )
    monkeypatch.setattr(
        service,
        "create_workflow",
        lambda *a: pytest.fail("write before matching preflight"),
    )
    with pytest.raises(DomainError):
        service.submit_workflow(f.session, f.p.owner_id, body, None)


def test_accepted_replay_uses_original_plan_before_current_profile_or_active_refs(
    declaration, monkeypatch
):
    f = declaration
    prepared = service.prepare_workflow(f.session, f.p.owner_id, f.request, None)
    body = WorkflowSubmit(
        idempotency_key="create-1",
        request=f.request,
        preflight_sha256=prepared.preflight.preflight_sha256,
    )
    internal = CreateWorkflow(
        idempotency_key=body.idempotency_key,
        plan=prepared.plan,
        public_submission_canonical=canonical_bytes(body).decode(),
    )
    f.session.scalar = lambda *a: NS(
        owner_id=f.p.owner_id,
        creation_canonical=canonical_bytes(internal).decode(),
        creation_key=body.idempotency_key,
        creation_sha256=digest(internal),
    )
    monkeypatch.setattr(
        service,
        "prepare_workflow",
        lambda *a: pytest.fail("current profile on accepted replay"),
    )
    monkeypatch.setattr(
        service, "create_workflow", lambda session, owner, value, settings: value
    )
    assert service.submit_workflow(f.session, f.p.owner_id, body, None) == internal
    changed = body.model_copy(
        update={
            "request": body.request.model_copy(
                update={"allow_workflow_development_feedback_release": True}
            )
        }
    )
    with pytest.raises(DomainError):
        service.submit_workflow(f.session, f.p.owner_id, changed, None)
