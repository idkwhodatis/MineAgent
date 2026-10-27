"""Actual HTTP+PostgreSQL queued declaration; deliberately no Docker execution."""

from uuid import UUID

import pytest
from rd_configured_dataset_fixture import persist_configured_dataset
from rd_submission_fixture import body, login, profile, submit
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.app.models import ExecutionAttempt, OutboxEvent, ResearchRun
from backend.app.rdagent_submission_models import FactorResearchSubmission
from workers.research.rdagent_queue import claim_configured_run

pytestmark = pytest.mark.postgres


@pytest.fixture
def queued(api, migrated_engine, settings, owner):
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    dataset, _source, policy = persist_configured_dataset(
        factory, owner.user.id, settings
    )
    settings.rdagent_research_enabled = True
    settings.rdagent_runtime_profile = profile(actual_images=False)
    headers = login(api, owner)
    receipt, exact = submit(api, headers, body(dataset, policy), key="http-stable-key")
    return factory, UUID(receipt["run_id"]), headers, exact, receipt


def test_http_submission_is_queued_without_synthetic_attempt_and_replays_after_disable(
    queued, api, settings
):
    factory, run_id, headers, exact, _receipt = queued
    with factory() as session:
        run = session.get(ResearchRun, run_id)
        assert (
            run.status == "queued"
            and run.generation == 1
            and run.active_attempt_id is None
        )
        assert session.scalar(select(func.count()).select_from(ExecutionAttempt)) == 0
        row = session.scalar(select(FactorResearchSubmission))
        assert (
            row.request_sha256 != run.request_sha256
            and row.config_sha256 == run.request_sha256
        )
        assert (
            session.scalar(select(OutboxEvent).filter_by(run_id=run_id, generation=1))
            is not None
        )
    settings.rdagent_research_enabled = False
    repeated = api.post(
        "/api/v1/engine-runs",
        headers={**headers, "Idempotency-Key": "http-stable-key"},
        json=exact,
    )
    assert (
        repeated.status_code == 201
        and repeated.json()["replayed"]
        and repeated.json()["run_id"] == str(run_id)
    )
    changed = {**exact, "preflight_sha256": "f" * 64}
    assert (
        api.post(
            "/api/v1/engine-runs",
            headers={**headers, "Idempotency-Key": "http-stable-key"},
            json=changed,
        ).status_code
        == 409
    )


def test_actual_first_claim_and_duplicate_are_distinct_from_queued_counter(
    queued, settings
):
    factory, run_id, *_ = queued
    fence = claim_configured_run(factory, run_id, 1, "pg-component-claim", settings)
    assert fence is not None and fence.generation == 1
    assert claim_configured_run(factory, run_id, 1, "duplicate", settings) is None
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionAttempt)) == 1
        run = session.get(ResearchRun, run_id)
        assert run.active_attempt_id == fence.attempt_id and run.status == "running"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE factor_research_submissions SET preflight_sha256=repeat('f',64)",
        "DELETE FROM factor_research_submissions",
        "DELETE FROM outbox_events",
        "UPDATE research_budget_controls SET limits=jsonb_set(limits,'{calls}','1'::jsonb)",
    ],
)
def test_direct_sql_cannot_sever_immutable_submission_graph(queued, sql):
    factory, *_ = queued
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(text(sql))
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def test_queued_cancel_is_authenticated_and_cannot_create_attempt(
    queued, api, settings
):
    factory, run_id, headers, _exact, _receipt = queued
    url = "/api/v1/engine-runs/" + str(run_id) + "/cancel"
    assert api.post(url).status_code == 401
    response = api.post(url, headers=headers)
    assert (
        response.status_code == 200
        and response.json()["status"] == "cancelled"
        and response.json()["pending"] is False
    )
    assert api.post(url, headers=headers).json() == response.json()
    assert claim_configured_run(factory, run_id, 1, "after-cancel", settings) is None
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionAttempt)) == 0


def test_claimed_cancel_without_any_physical_admission_revokes_real_fence(
    queued, api, settings
):
    from backend.app.research_lifecycle import neutral_transaction
    from backend.app.research_process_models import ResearchProcessAdmission
    from backend.app.services import DomainError
    from workers.research.rdagent_recovery import reconcile_rd_attempt

    factory, run_id, headers, *_ = queued
    fence = claim_configured_run(factory, run_id, 1, "cancel-before-launch", settings)
    response = api.post(f"/api/v1/engine-runs/{run_id}/cancel", headers=headers)
    assert response.status_code == 200 and response.json()["pending"] is True
    assert reconcile_rd_attempt(factory, fence.attempt_id, settings)
    assert not reconcile_rd_attempt(factory, fence.attempt_id, settings)
    with pytest.raises(DomainError), neutral_transaction(factory, fence):
        pytest.fail("Revoked old attempt must not regain authority")
    with factory() as session:
        run = session.get(ResearchRun, run_id)
        attempt = session.get(ExecutionAttempt, fence.attempt_id)
        assert run.status == attempt.status == "cancelled" and attempt.sandbox_stopped
        assert (
            session.scalar(select(func.count()).select_from(ResearchProcessAdmission))
            == 0
        )
