"""Authority-double tests; real SO_PEERCRED/PG acceptance remains separate."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import backend.app.engine_peer_services as peers
import backend.app.rdagent_controller_services as service
from backend.app.rdagent_models import FactorControllerLaunch
from backend.app.services import DomainError
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError
from runtimes.rdagent.probe_fixture import research_dict


def fixture():
    config = FactorResearchConfig.model_validate(research_dict())
    fence = AttemptFence(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        lease_token=UUID(int=4),
    )
    attempt = SimpleNamespace(id=UUID(int=5))
    bootstrap = RDOperationalBootstrap(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=1,
        spec_id=UUID(int=6),
        attempt_binding_id=attempt.id,
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    launch = SimpleNamespace(
        attempt_binding_id=attempt.id,
        admission_id=UUID(int=7),
        bootstrap=bootstrap.model_dump(mode="json"),
        bootstrap_sha256=bootstrap.sha256,
    )
    return fence, attempt, bootstrap, launch


def test_launch_mapping_is_one_per_attempt_and_admission():
    ddl = str(
        CreateTable(FactorControllerLaunch.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    for field in ("attempt_id", "attempt_binding_id", "admission_id"):
        assert f"UNIQUE ({field})" in ddl


def test_controller_binding_comes_from_host_locked_rows_not_event(monkeypatch):
    fence, attempt, bootstrap, launch = fixture()
    monkeypatch.setattr(
        service, "expected_operational_bootstrap", lambda *args: (attempt, bootstrap)
    )
    session = SimpleNamespace(scalar=lambda query: launch)
    authorization = object()
    seen = []

    def resolve(current, current_fence, auth, *, binding):
        assert current is session and current_fence == fence and auth is authorization
        seen.append(binding)
        return SimpleNamespace(logical_operation_id=attempt.id)

    monkeypatch.setattr(peers, "require_controller_peer", resolve)
    assert service.require_rd_controller(session, fence, authorization) is launch
    assert seen[0].admission_id == launch.admission_id
    assert seen[0].bootstrap_sha256 == bootstrap.sha256
    assert seen[0].role == "rd_orchestrator"


@pytest.mark.parametrize(
    "drift",
    [
        "missing",
        "attempt_binding",
        "bootstrap_hash",
        "bootstrap_body",
        "logical_operation",
        "unauthenticated",
    ],
)
def test_controller_fails_closed_before_domain_event(monkeypatch, drift):
    fence, attempt, bootstrap, launch = fixture()
    monkeypatch.setattr(
        service, "expected_operational_bootstrap", lambda *args: (attempt, bootstrap)
    )
    if drift == "attempt_binding":
        launch.attempt_binding_id = UUID(int=99)
    elif drift == "bootstrap_hash":
        launch.bootstrap_sha256 = "0" * 64
    elif drift == "bootstrap_body":
        launch.bootstrap = {**launch.bootstrap, "generation": 2}
    session = SimpleNamespace(
        scalar=lambda query: None if drift == "missing" else launch
    )

    def resolve(*args, **kwargs):
        if drift == "unauthenticated":
            raise ProcessError("engine_controller_peer")
        return SimpleNamespace(
            logical_operation_id=UUID(int=99)
            if drift == "logical_operation"
            else attempt.id
        )

    monkeypatch.setattr(peers, "require_controller_peer", resolve)
    with pytest.raises((DomainError, ProcessError)):
        service.require_rd_controller(session, fence, object())


def test_authenticated_call_does_not_reach_mutation_on_denied_peer(monkeypatch):
    def denied(*args):
        raise ProcessError("engine_controller_peer")

    monkeypatch.setattr(service, "require_rd_controller", denied)
    import backend.app.rdagent_services as domain

    monkeypatch.setattr(
        domain,
        "open_upstream_call",
        lambda *args, **kwargs: pytest.fail("denied peer mutated domain"),
    )
    with pytest.raises(ProcessError):
        service.authenticated_open_upstream_call(
            object(),
            object(),
            object(),
            start_id=UUID(int=1),
            call_id=UUID(int=2),
            phase="generation",
            kind="chat",
        )


@pytest.mark.parametrize(
    "mode", ["new", "replay", "foreign_operation", "changed_launch"]
)
def test_host_launch_binding_is_flush_only_and_immutable(monkeypatch, mode):
    import backend.app.rdagent_services as domain

    fence, attempt, bootstrap, launch = fixture()
    admission = SimpleNamespace(
        role="rd_orchestrator",
        profile="mineagent.rdagent-orchestrator.v1",
        image_id=bootstrap.config.runtime.orchestrator_image,
        input_sha256=bootstrap.sha256,
        logical_operation_id=attempt.id,
    )
    if mode == "foreign_operation":
        admission.logical_operation_id = UUID(int=99)
    if mode == "changed_launch":
        launch.bootstrap_sha256 = "0" * 64
    monkeypatch.setattr(
        service, "expected_operational_bootstrap", lambda *args: (attempt, bootstrap)
    )
    monkeypatch.setattr(domain, "_scope_row", lambda *args: admission)
    added, flushes = [], []
    session = SimpleNamespace(
        scalar=lambda query: None if mode == "new" else launch,
        add=added.append,
        flush=lambda: flushes.append(True),
    )
    if mode in {"foreign_operation", "changed_launch"}:
        with pytest.raises(DomainError):
            service.bind_controller_launch(
                session, fence, admission_id=launch.admission_id
            )
        assert not added and not flushes
    else:
        result = service.bind_controller_launch(
            session, fence, admission_id=launch.admission_id
        )
        assert result.admission_id == launch.admission_id
        assert len(added) == len(flushes) == (1 if mode == "new" else 0)


@pytest.mark.parametrize(
    "mode",
    [
        "complete",
        "abort_empty",
        "empty_success",
        "truncated",
        "pending_response",
        "terminal",
        "bool_coercion",
    ],
)
def test_authenticated_exchange_boundary_requires_real_response_closure(
    monkeypatch, mode
):
    import backend.app.rdagent_services as domain

    fence, _, _, _ = fixture()
    launch = SimpleNamespace(id=UUID(int=10))
    call = SimpleNamespace(id=UUID(int=11), start_id=UUID(int=12), kind="chat")
    members = (
        []
        if mode in {"abort_empty", "empty_success"}
        else [SimpleNamespace(id=UUID(int=13))]
    )
    monkeypatch.setattr(service, "require_rd_controller", lambda *args: launch)
    monkeypatch.setattr(domain, "_scope_row", lambda *args, **kwargs: call)
    monkeypatch.setattr(domain, "_call_members", lambda *args: members)

    def response(*args):
        if mode == "pending_response":
            raise DomainError("factor_model_operation_incomplete", "pending", 409)
        return SimpleNamespace(id=UUID(int=14)), SimpleNamespace(
            finish_reason="length" if mode == "truncated" else "stop"
        )

    monkeypatch.setattr(domain, "_completed_member_response", response)
    added = []
    answers = iter([None, object() if mode == "terminal" else None])
    session = SimpleNamespace(
        scalar=lambda query: next(answers), add=added.append, flush=lambda: None
    )
    succeeded = 1 if mode == "bool_coercion" else mode != "abort_empty"
    if mode not in {"complete", "abort_empty"}:
        with pytest.raises(DomainError):
            service.authenticated_close_upstream_call(
                session,
                fence,
                object(),
                call_id=call.id,
                succeeded=succeeded,
                settings=None,
            )
        assert not added
    else:
        result = service.authenticated_close_upstream_call(
            session,
            fence,
            object(),
            call_id=call.id,
            succeeded=succeeded,
            settings=None,
        )
        assert result.status == ("completed" if mode == "complete" else "aborted")
        assert result.member_count == len(members)
        assert len(added) == 1
