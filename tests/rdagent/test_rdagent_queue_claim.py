"""First-claim control-flow doubles; actual PG concurrency is a separate gate."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_submission_receipts import replay  # noqa:F401

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ExecutionAttempt, ResearchRun
from backend.app.services import DomainError
from packages.rdagent_adapter.persisted_identity import require_source_identity
from workers.research import rdagent_queue as queue


@pytest.fixture(
    params=[
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
    ]
)
def delivery(request, replay, monkeypatch):  # noqa:F811
    r = replay
    r.run.status = "queued"
    r.run.generation = 1
    r.run.active_attempt_id = None
    r.added = []
    r.locks = []
    results = iter(
        [
            r.owner,
            r.run,
            None,
            r.row,
            SimpleNamespace(run_id=r.run.id, generation=1),
            r.row,
        ]
    )

    class Session:
        def scalars(self, query):
            return SimpleNamespace(all=lambda: [request.param])

        def scalar(self, query):
            return next(results)

        def get(self, model, identity):
            if model is ResearchRun:
                return r.run
            assert model is FactorAnalysisPlan
            return r.plan

        def add(self, row):
            r.added.append(row)

        def flush(self):
            pass

    r.session = Session()
    r.settings = SimpleNamespace(lease_seconds=60)
    r.runtime = require_source_identity(r.run).runtime
    monkeypatch.setattr(
        queue, "_try_neutral_lock", lambda session, scope: r.locks.append(scope)
    )
    monkeypatch.setattr(
        queue,
        "preflight",
        lambda *args: SimpleNamespace(
            data_eligible=True, can_submit=True, preflight_sha256=r.row.preflight_sha256
        ),
    )
    return r


def claim(r):
    return queue._claim(
        r.session,
        r.run.id,
        1,
        "worker:actual-queue",
        r.settings,
        r.runtime,
        r.plan.qlib_installed_parent_image_id,
    )


def test_first_claim_creates_actual_attempt_then_fence(delivery):
    r = delivery
    fence = claim(r)
    assert len(r.added) == 1 and isinstance(r.added[0], ExecutionAttempt)
    attempt = r.added[0]
    assert (fence.run_id, fence.attempt_id, fence.generation, fence.lease_token) == (
        r.run.id,
        attempt.id,
        1,
        attempt.lease_token,
    )
    assert r.run.active_attempt_id == attempt.id and r.run.status == "running"
    assert not attempt.sandbox_stopped and attempt.finished_at is None
    assert r.locks == [f"submit:{r.owner}"]


@pytest.mark.parametrize(
    "status",
    ["running", "cancel_requested", "cancelled", "succeeded", "failed", "interrupted"],
)
def test_duplicate_or_controlled_delivery_never_creates_attempt(delivery, status):
    r = delivery
    r.run.status = status
    assert claim(r) is None and not r.added


def test_existing_active_attempt_not_recreated(delivery):
    r = delivery
    r.run.active_attempt_id = uuid4()
    assert claim(r) is None and not r.added


def test_future_generation_not_silent_resume(delivery):
    r = delivery
    with pytest.raises(DomainError):
        queue._claim(
            r.session,
            r.run.id,
            2,
            "worker",
            r.settings,
            r.runtime,
            r.plan.qlib_installed_parent_image_id,
        )
    assert not r.added and not r.locks


def test_source_drift_rejected_before_any_attempt(delivery, monkeypatch):
    r = delivery
    monkeypatch.setattr(
        queue,
        "preflight",
        lambda *args: SimpleNamespace(
            data_eligible=False,
            can_submit=False,
            preflight_sha256=r.row.preflight_sha256,
        ),
    )
    with pytest.raises(DomainError) as exc:
        claim(r)
    assert exc.value.code == "preflight_changed" and not r.added


def test_runtime_changed_rejected_before_attempt(delivery):
    r = delivery
    r.plan.qlib_installed_parent_image_id = "sha256:" + "b" * 64
    with pytest.raises(DomainError):
        claim(r)
    assert not r.added
