"""Closed public admission and exact-replay ordering; no PostgreSQL claim."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_submission_services as service
from backend.app.services import DomainError


class Session:
    def __init__(self, revision=service.REVISION):
        self.revision = revision

    def scalars(self, query):
        return SimpleNamespace(all=lambda: [self.revision])


def test_closed_runtime_never_treats_environment_pin_as_acceptance():
    with pytest.raises(DomainError) as exc:
        service.resolve_submission_runtime(
            SimpleNamespace(MINEAGENT_ORCHESTRATOR_IMAGE="sha256:" + "a" * 64)
        )
    assert exc.value.code == "engine_submission_not_enabled"


def test_exact_replay_precedes_current_runtime_and_source(monkeypatch):
    calls = []
    receipt = object()
    monkeypatch.setattr(
        service, "_try_neutral_lock", lambda session, key: calls.append(key)
    )
    monkeypatch.setattr(service, "existing_receipt", lambda *args: receipt)

    def forbidden(*args):
        raise AssertionError("no runtime or source work on accepted replay")

    monkeypatch.setattr(service, "resolve_submission_runtime", forbidden)
    monkeypatch.setattr(service, "preflight", forbidden)
    owner = uuid4()
    assert (
        service.submit_configured_run(
            Session(), owner, object(), "stable-key", object()
        )
        is receipt
    )
    assert calls == [f"submit:{owner}"]


def test_wrong_schema_does_not_query_uninstalled_receipt(monkeypatch):
    monkeypatch.setattr(
        service, "existing_receipt", lambda *args: pytest.fail("absent table lookup")
    )
    with pytest.raises(DomainError):
        service.submit_configured_run(
            Session("022_factor_analysis"), uuid4(), object(), "key", object()
        )


def test_new_submission_stays_closed_after_shared_key_check(monkeypatch):
    from backend.app import comparison_services

    calls = []
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *args: None)
    monkeypatch.setattr(service, "existing_receipt", lambda *args: None)
    monkeypatch.setattr(
        comparison_services,
        "reject_comparison_key",
        lambda *args: calls.append("shared-key"),
    )
    monkeypatch.setattr(
        service,
        "_persist_queued",
        lambda *args: pytest.fail("unaccepted catalog wrote queued run"),
    )
    with pytest.raises(DomainError) as exc:
        service.submit_configured_run(Session(), uuid4(), object(), "new-key", object())
    assert calls == ["shared-key"] and exc.value.code == "engine_submission_not_enabled"


# Real retained-file fixture, transaction/lock doubles only; not PG acceptance.
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401


@pytest.fixture
def queued_writer(setup_source, request_body, monkeypatch):  # noqa:F811
    from backend.app.engine_run_preflight import preflight
    from backend.app.engine_run_submission_schemas import RDSubmit
    from packages.factors.contracts import canonical_bytes

    s = setup_source
    request_body = request_body.model_copy(
        update={
            "input_fields": ("close",),
            "task": request_body.task.model_copy(
                update={
                    "factor_kind": "time_series",
                    "formulation": "close[t]/close[t-1]-1",
                    "output_unit": "return",
                }
            ),
        }
    )
    from backend.app.rdagent_server_profile import (
        ROLE_LOCKS,
        RDServerRuntimeProfile,
    )
    from packages.rdagent_adapter.contracts import DependencyLock

    runtime = s.config.runtime.model_copy(
        update={
            "dependency_locks": tuple(
                DependencyLock(role=role, sha256=sha)
                for role, sha in sorted(ROLE_LOCKS.items())
            )
        }
    )
    s.config = s.config.model_copy(update={"runtime": runtime})
    s.session.scalars = lambda query: SimpleNamespace(all=lambda: [service.REVISION])
    s.settings.rdagent_research_enabled = True
    s.settings.rdagent_runtime_profile = RDServerRuntimeProfile(
        runtime=runtime, qlib_installed_parent_image_id="sha256:" + "e" * 64
    )
    original_preflight = service.preflight
    original_source = service.resolve_dataset_preflight
    monkeypatch.setattr(
        service,
        "preflight",
        lambda session, *args: original_preflight(s.session, *args),
    )
    monkeypatch.setattr(
        service,
        "resolve_dataset_preflight",
        lambda session, **kwargs: original_source(s.session, **kwargs),
    )
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *args: None)
    monkeypatch.setattr(service, "quota_limit", lambda *args: 1)
    monkeypatch.setattr(service, "_identities", lambda *args: [])

    class WriteSession(Session):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.active = 0

        def scalar(self, query):
            return self.active

        def get(self, model, identity):
            assert identity == s.dataset.id
            return s.dataset

        def add(self, row):
            self.rows.append(row)

        def flush(self):
            pass

    checked = preflight(s.session, s.fence.owner_id, request_body, s.settings)
    assert checked.data_eligible
    body = RDSubmit(
        **request_body.model_dump(), preflight_sha256=checked.preflight_sha256
    )
    return SimpleNamespace(
        session=WriteSession(),
        source=s,
        body=body,
        canonical=canonical_bytes(request_body).decode(),
    )


def test_real_retained_source_queued_declaration_has_no_attempt_or_fence(queued_writer):
    from backend.app.factor_analysis_models import FactorAnalysisPlan
    from backend.app.models import DataUsageExposure, ExecutionAttempt, ResearchRun
    from backend.app.rdagent_submission_models import FactorResearchSubmission

    q = queued_writer
    result = service._persist_queued(
        q.session,
        q.source.fence.owner_id,
        q.body,
        "queued-key",
        q.source.settings,
        q.source.config.runtime,
        "sha256:" + "e" * 64,
    )
    runs = [x for x in q.session.rows if isinstance(x, ResearchRun)]
    assert (
        len(runs) == 1 and runs[0].generation == 1 and runs[0].active_attempt_id is None
    )
    assert runs[0].status == "queued" and result.status == "queued"
    assert not any(isinstance(x, ExecutionAttempt) for x in q.session.rows)
    plan = next(x for x in q.session.rows if isinstance(x, FactorAnalysisPlan))
    receipt = next(x for x in q.session.rows if isinstance(x, FactorResearchSubmission))
    exposure = next(x for x in q.session.rows if isinstance(x, DataUsageExposure))
    assert receipt.request_canonical == q.canonical and receipt.plan_id == plan.id
    assert runs[0].request_sha256 == receipt.config_sha256 != receipt.request_sha256
    assert exposure.run_id == runs[0].id and exposure.provenance["plan_id"] == str(
        plan.id
    )
    assert exposure.end_date == q.source.dataset.end_date


def test_quota_failure_writes_nothing(queued_writer):
    q = queued_writer
    q.session.active = 1
    with pytest.raises(DomainError) as exc:
        service._persist_queued(
            q.session,
            q.source.fence.owner_id,
            q.body,
            "queued-key",
            q.source.settings,
            q.source.config.runtime,
            "sha256:" + "e" * 64,
        )
    assert exc.value.code == "run_quota" and not q.session.rows


def test_changed_preflight_writes_nothing(queued_writer):
    q = queued_writer
    changed = q.body.model_copy(update={"preflight_sha256": "f" * 64})
    with pytest.raises(DomainError) as exc:
        service._persist_queued(
            q.session,
            q.source.fence.owner_id,
            changed,
            "queued-key",
            q.source.settings,
            q.source.config.runtime,
            "sha256:" + "e" * 64,
        )
    assert exc.value.code == "preflight_changed" and not q.session.rows
