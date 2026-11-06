"""Worker terminal state and HTTP serialization with explicit lifecycle doubles.

No PostgreSQL, Docker, provider execution or authentication acceptance is claimed.
"""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import get_current_user, get_session
from backend.app.engine_run_routes import router
from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.research_broker_models import ResearchBudgetReservation
from backend.app.research_lifecycle import _NEUTRAL_KEY
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent.probe_fixture import research_dict
from workers.research import rdagent_operational as worker


@pytest.fixture
def case(monkeypatch, tmp_path):
    config = FactorResearchConfig.model_validate(research_dict())
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    binding = NS(id=uuid4())
    bootstrap = RDOperationalBootstrap(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=1,
        spec_id=uuid4(),
        attempt_binding_id=binding.id,
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    now = datetime.now(UTC)
    run = ResearchRun(
        id=fence.run_id,
        owner_id=fence.owner_id,
        purpose="factor_research",
        engine="rdagent",
        config=config.model_dump(mode="json"),
        request_sha256=config.sha256,
        dataset_version_id=config.input.dataset.dataset_version_id,
        status="running",
        generation=1,
        active_attempt_id=fence.attempt_id,
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    attempt = ExecutionAttempt(id=fence.attempt_id, status="running")
    held = NS(id=uuid4(), amounts=BudgetVector().model_dump())
    session = MagicMock()
    session.info = {_NEUTRAL_KEY: {}}
    session.get.side_effect = lambda model, identity: {
        ResearchRun: run,
        ExecutionAttempt: attempt,
        ResearchBudgetReservation: held,
    }[model]
    states = [NS(status="stopped")]
    session.scalars.return_value = states
    outcome = NS(outcome="accepted")
    session.scalar.return_value = outcome
    factory = NS(kw={"bind": object()})
    settings = NS(storage_root=tmp_path)
    monkeypatch.setattr(worker, "utcnow", lambda: now)
    monkeypatch.setattr(worker, "shared_lifecycle", lambda *a: nullcontext())
    monkeypatch.setattr(
        worker, "neutral_transaction", lambda *a, **kw: nullcontext(session)
    )
    monkeypatch.setattr(
        worker, "expected_operational_bootstrap", lambda *a: (binding, bootstrap)
    )
    monkeypatch.setattr(worker, "_image_inventory", lambda *a: {})
    monkeypatch.setattr(worker, "reserve_budget", lambda *a, **kw: held)
    for name in (
        "persist_controller_intent",
        "bind_controller_launch",
        "check_controller_claim",
        "record_controller_stopped",
        "settle_budget",
        "_issue_committed_authority",
    ):
        monkeypatch.setattr(worker, name, MagicMock())
    monkeypatch.setattr(worker, "claim_controller_launch", MagicMock())
    recover = MagicMock()
    monkeypatch.setattr(worker, "_recover_and_record", recover)
    running = MagicMock()
    running.accept_controller.return_value = (None, None)
    running.original_deadline_completion = True
    running.measured_exit_code = 0
    running.finish.side_effect = lambda *, on_stopped: on_stopped(None)
    supervisor = MagicMock()
    supervisor.start.return_value = running
    monkeypatch.setattr(worker, "ControllerSupervisor", lambda *a: supervisor)
    handler = NS(returned=True)
    serve = MagicMock(side_effect=RuntimeError("Private provider diagnostic"))
    monkeypatch.setattr(worker, "serve_one", serve)
    from backend.app import rdagent_provider_containment

    contained = MagicMock(return_value=True)
    monkeypatch.setattr(
        rdagent_provider_containment, "provider_attempt_stopped", contained
    )

    def execute():
        return worker._execute_controller(
            factory, fence, settings, handler_type=lambda *a: handler
        )

    # Exercise the actual engine-run HTTP route and response model; only its
    # owner/session dependencies are doubles, separate from the worker session.
    api_session = MagicMock()
    api_session.scalar.return_value = run
    api_session.scalars.return_value.all.return_value = ["021_execution_snapshots"]
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: NS(id=run.owner_id)
    app.dependency_overrides[get_session] = lambda: api_session
    with TestClient(app) as api:
        yield NS(
            execute=execute,
            run=run,
            attempt=attempt,
            session=session,
            now=now,
            api=api,
            supervisor=supervisor,
            running=running,
            handler=handler,
            outcome=outcome,
            states=states,
            contained=contained,
            recover=recover,
        )


@pytest.mark.parametrize("failure", ["start", "rpc", "incomplete"])
def test_failed_controller_exposes_sanitized_error_and_terminal_time(case, failure):
    if failure == "start":
        case.supervisor.start.side_effect = RuntimeError("Private provider diagnostic")
    elif failure == "rpc":
        case.handler.returned = False
    else:
        case.outcome.outcome = "rejected"
    with pytest.raises((RuntimeError, ProcessError)):
        case.execute()
    assert case.run.status == case.attempt.status == "failed"
    assert case.run.error == case.attempt.error == "rd_controller_execution_failed"
    assert case.run.updated_at == case.attempt.finished_at == case.now
    assert case.session.info[_NEUTRAL_KEY]["terminal"][0] == "rd_controller_failed"
    response = case.api.get(f"/api/v1/engine-runs/{case.run.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "rd_controller_execution_failed"
    assert datetime.fromisoformat(response.json()["updated_at"]) == case.now
    assert "Private provider diagnostic" not in response.text


def test_successful_controller_updates_terminal_time_without_failure_error(case):
    assert case.execute() == case.run.id
    assert case.run.status == case.attempt.status == "succeeded"
    assert case.run.error is case.attempt.error is None
    assert case.run.updated_at == case.attempt.finished_at == case.now
    assert case.session.info[_NEUTRAL_KEY]["terminal"][0] == "rd_controller_succeeded"


@pytest.mark.parametrize("uncertainty", ["controller", "provider", "cleanup"])
def test_unknown_containment_does_not_publish_terminal_failure(case, uncertainty):
    original_updated_at = case.run.updated_at
    if uncertainty == "controller":
        case.states.clear()
    elif uncertainty == "provider":
        case.contained.return_value = False
    else:
        case.supervisor.start.side_effect = RuntimeError("Private provider diagnostic")
        case.recover.side_effect = ProcessError("uncertain_cleanup")
    with pytest.raises((RuntimeError, ProcessError)):
        case.execute()
    assert case.run.status == case.attempt.status == "running"
    assert case.run.error is case.attempt.error is None
    assert case.run.updated_at == original_updated_at
    assert case.attempt.finished_at is None
    assert "terminal" not in case.session.info[_NEUTRAL_KEY]
