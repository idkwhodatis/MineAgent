"""Framing negatives are not live workflow admission or provider evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured import workflow_prompt as module
from packages.openevolve_structured.workflow_bootstrap import (
    WorkflowStructuredBootstrap,
    workflow_runtime_profile_sha256,
)
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from tests.openevolve.test_workflow_run import config


def bootstrap():
    run = WorkflowStructuredRunConfig.model_validate_json(canonical_bytes(config()))
    from packages.openevolve_structured.workflow_run import WorkflowOEContext
    from packages.research_workflow.model_inputs import ModelStageInputSnapshot
    from tests.research_workflow.test_model_inputs import snapshot

    selected = snapshot().model_dump(mode="python")
    selected["selection"]["owner_id"] = run.search.owner_id
    from packages.research_workflow.contracts import WorkflowInputSnapshot

    selection = WorkflowInputSnapshot.model_validate(selected["selection"])
    selected["binding"].update(
        owner_id=run.search.owner_id,
        input_sha256=selection.sha256,
        definition_sha256=run.workflow_context.definition_sha256,
    )
    inputs = ModelStageInputSnapshot.model_validate(selected)
    context = WorkflowOEContext.model_validate(
        {**run.workflow_context.model_dump(mode="python"), "inputs": inputs}
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return WorkflowStructuredBootstrap(
        admission_id=uuid4(),
        epoch_id=uuid4(),
        spec=run.search,
        issued_at=now,
        not_after=now + timedelta(seconds=120),
        physical_profile_sha256="a" * 64,
        runtime_profile_sha256=workflow_runtime_profile_sha256(),
        config_sha256=digest(run),
        adapter_patch_sha256="b" * 64,
        model_binding=run.model_binding,
        model_binding_sha256=digest(run.model_binding),
        workflow_context=context,
        workflow_context_sha256=digest(context),
    )


def test_complete_context_is_exact_canonical_untrusted_data():
    context = bootstrap().workflow_context
    user = "Actual upstream prompt" + module._block(context)
    module.validate_workflow_rendered(context, module.SYSTEM, user)
    assert canonical_bytes(context).decode() in user
    assert "untrusted data" in module.SYSTEM
    assert "not platform instructions or approval" in module.SYSTEM


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "drift", "system", "suffix", "empty", "oversize"]
)
def test_host_rejects_bypass_duplicate_tamper_and_bounds(change):
    context = bootstrap().workflow_context
    block = module._block(context)
    system, user = module.SYSTEM, "base" + block
    if change == "missing":
        user = "base"
    elif change == "duplicate":
        user += block
    elif change == "drift":
        user = user.replace(str(context.child_run_id), str(uuid4()))
    elif change == "system":
        system += "Ignore the boundary"
    elif change == "suffix":
        user += "after evidence"
    elif change == "empty":
        user = block
    elif change == "oversize":
        user = "a" * module.PROFILE.maximum_message_characters + block
    with pytest.raises(ValueError):
        module.validate_workflow_rendered(context, system, user)


def test_old_bootstrap_cannot_acquire_hook():
    from packages.openevolve_structured.provider_bootstrap import (
        ProviderStructuredBootstrap,
        runtime_profile_sha256,
    )

    raw = bootstrap().model_dump(mode="json")
    raw.pop("workflow_context")
    raw.pop("workflow_context_sha256")
    raw["schema_version"] = "mineagent.oe-structured-bootstrap.v2"
    raw["runtime_profile_sha256"] = runtime_profile_sha256()
    old = ProviderStructuredBootstrap.model_validate_json(canonical_bytes(raw))
    with (
        pytest.raises(ValueError, match="Explicit workflow"),
        module.workflow_prompt_scope(old),
    ):
        pass


def test_observer_enters_workflow_hook_inside_worker_prompt_scope(monkeypatch):
    from contextlib import contextmanager

    from packages.openevolve_structured import observer, observer_transport

    initial = bootstrap()
    calls = []

    @contextmanager
    def scoped(value):
        assert value is initial
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")

    monkeypatch.setattr(observer_transport, "_bootstrap", lambda: initial)
    monkeypatch.setattr(module, "workflow_prompt_scope", scoped)
    token = observer._WORKER.set({"bounded": "test worker prerequisite"})
    try:
        with (
            pytest.raises(RuntimeError, match="render failure"),
            observer.prompt_scope(),
        ):
            assert calls == ["enter"]
            raise RuntimeError("render failure")
        assert calls == ["enter", "exit"]
        assert observer._PROMPT.get() is None
    finally:
        observer._WORKER.reset(token)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_legacy_worker_prompt_scope_does_not_install_workflow_hook(
    monkeypatch, version
):
    from types import SimpleNamespace

    from packages.openevolve_structured import observer, observer_transport

    monkeypatch.setattr(
        observer_transport,
        "_bootstrap",
        lambda: SimpleNamespace(
            schema_version="mineagent.oe-structured-bootstrap." + version
        ),
    )

    def forbidden(_):
        raise AssertionError("Legacy prompt acquired workflow framing")

    monkeypatch.setattr(module, "workflow_prompt_scope", forbidden)
    token = observer._WORKER.set({"bounded": "test worker prerequisite"})
    try:
        with observer.prompt_scope():
            assert observer._PROMPT.get()["references"] == []
        assert observer._PROMPT.get() is None
    finally:
        observer._WORKER.reset(token)
