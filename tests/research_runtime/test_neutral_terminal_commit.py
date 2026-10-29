"""Actual SQLAlchemy commit veto; SQL rows/locks and physical proofs are doubles."""

from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app import research_lifecycle as lifecycle
from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence

FAMILIES = {
    "structured_succeeded": ("factor_strategy", "structured", "succeeded", None),
    "oe_structured_succeeded": ("strategy_search", "openevolve", "succeeded", None),
    "oe_initial_succeeded": ("strategy_search", "openevolve", "succeeded", 124),
    "rd_controller_succeeded": ("factor_research", "rdagent", "succeeded", 124),
    "rd_controller_failed": ("factor_research", "rdagent", "failed", None),
}


def arranged(monkeypatch, family="structured_succeeded"):
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    purpose, engine, _, _ = FAMILIES[family]
    run = NS(
        id=fence.run_id,
        owner_id=fence.owner_id,
        active_attempt_id=fence.attempt_id,
        generation=1,
        status="running",
        purpose=purpose,
        engine=engine,
        config={
            "schema_version": (
                "mineagent.oe-structured-provider-run.v1"
                if family == "oe_structured_succeeded"
                else "mineagent.strategy-search.v1"
            )
        },
    )
    attempt = NS(
        id=fence.attempt_id,
        run_id=fence.run_id,
        generation=1,
        lease_token=fence.lease_token,
        lease_expires_at=utcnow() + timedelta(seconds=60),
        status="running",
        finished_at=None,
        sandbox_stopped=False,
        exit_code=None,
        error=None,
    )
    snapshots = (uuid4(), uuid4())
    executions = [NS(id=uuid4(), snapshot_id=s) for s in snapshots]
    results = [NS(admission_id=uuid4()) for _ in executions]
    commits = []

    class Rows(Session):
        def scalar(self, statement, *args, **kwargs):
            return {ResearchRun: run, ExecutionAttempt: attempt}[
                statement.column_descriptions[0]["entity"]
            ]

        def get(self, model, key, **kwargs):
            return {ResearchRun: run, ExecutionAttempt: attempt}[model]

        def scalars(self, statement, *args, **kwargs):
            name = statement.column_descriptions[0]["entity"].__name__
            return (
                executions
                if name == "StructuredExecution"
                else [
                    NS(admission_id=r.admission_id, status="stopped") for r in results
                ]
            )

    db = create_engine("sqlite://")
    factory = sessionmaker(bind=db, class_=Rows)
    event.listen(Rows, "after_commit", lambda _: commits.append(True))
    monkeypatch.setattr(lifecycle, "_try_neutral_lock", lambda *a: None)
    import backend.app.oe_structured_identity as structured_oe
    import backend.app.structured_factor_services as structured
    import packages.openevolve_adapter.persisted_identity as oe
    import packages.rdagent_adapter.persisted_identity as rd

    monkeypatch.setattr(structured_oe, "require_run_identity", lambda *_: None)
    monkeypatch.setattr(structured, "require_structured_identity", lambda *_: None)
    monkeypatch.setattr(rd, "require_source_identity", lambda *_: None)
    monkeypatch.setattr(oe, "require_search_identity", lambda *_: None)
    return factory, fence, run, attempt, snapshots, executions, results, commits


def stage(session, family, run, attempt):
    _, _, status, exit_code = FAMILIES[family]
    run.status = attempt.status = status
    attempt.finished_at = utcnow()
    attempt.sandbox_stopped = True
    attempt.exit_code = exit_code
    if status == "failed":
        attempt.error = "rd_controller_execution_failed"
    marker = (family, run.id, attempt.id, attempt.finished_at)
    if family not in {"structured_succeeded", "oe_structured_succeeded"}:
        marker += (exit_code,)
    session.info[lifecycle._NEUTRAL_KEY]["terminal"] = marker


@pytest.mark.parametrize("family", FAMILIES)
def test_explicit_terminal_commits_but_cannot_authorize_more_work(monkeypatch, family):
    factory, fence, run, attempt, *_, commits = arranged(monkeypatch, family)
    with lifecycle.neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        stage(session, family, run, attempt)
        with pytest.raises(DomainError):
            lifecycle.require_neutral_transaction(session, fence)
    assert commits == [True]


@pytest.mark.parametrize(
    "defect",
    [
        "unstaged",
        "lease",
        "token",
        "generation",
        "owner",
        "status",
        "stop",
        "exit",
        "finished",
        "family",
    ],
)
@pytest.mark.parametrize(
    "family",
    ["structured_succeeded", "rd_controller_succeeded", "oe_initial_succeeded"],
)
def test_real_commit_veto_rejects_unstaged_or_changed_final_state(
    monkeypatch, family, defect
):
    factory, fence, run, attempt, *_, commits = arranged(monkeypatch, family)
    with (
        pytest.raises(DomainError),
        lifecycle.neutral_transaction(factory, fence) as session,
    ):
        stage(session, family, run, attempt)
        if defect == "unstaged":
            session.info[lifecycle._NEUTRAL_KEY].pop("terminal")
        elif defect == "lease":
            attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
        elif defect == "token":
            attempt.lease_token = uuid4()
        elif defect == "generation":
            attempt.generation += 1
        elif defect == "owner":
            run.owner_id = uuid4()
        elif defect == "status":
            run.status = "cancel_requested"
        elif defect == "stop":
            attempt.sandbox_stopped = False
        elif defect == "exit":
            attempt.exit_code = 0
        elif defect == "finished":
            attempt.finished_at += timedelta(seconds=1)
        elif defect == "family":
            session.info[lifecycle._NEUTRAL_KEY]["terminal"] = (
                "unknown",
                run.id,
                attempt.id,
                attempt.finished_at,
            )
    assert not commits


def test_actual_structured_finalizer_survives_real_transaction_exit(
    monkeypatch, tmp_path
):
    from backend.app import (
        structured_factor_results as readers,
    )
    from backend.app import (
        structured_factor_services as services,
    )
    from workers.research import structured_factor as worker

    factory, fence, run, attempt, snapshots, executions, results, commits = arranged(
        monkeypatch
    )
    from backend.app.core import maintenance

    monkeypatch.setattr(maintenance, "shared_lifecycle", lambda *_: nullcontext())
    monkeypatch.setattr(worker, "native_storage_guard", lambda _: nullcontext())
    monkeypatch.setattr(
        services, "_spec", lambda *_: (run, None, NS(snapshots=snapshots), None)
    )
    observed = []

    def read(session, supplied, execution_id, settings):
        lifecycle.require_neutral_transaction(
            session, supplied, resource_scope="factor_numeric"
        )
        observed.append(execution_id)
        return results[[x.id for x in executions].index(execution_id)]

    monkeypatch.setattr(readers, "read_verified_structured_execution", read)
    assert worker.finish_structured_acceptance_run(
        factory, fence, NS(storage_root=tmp_path)
    ) == tuple(results)
    assert observed == [x.id for x in executions]
    assert commits == [True] and run.status == attempt.status == "succeeded"


@pytest.mark.parametrize(
    "actual,wrong",
    [
        ("oe_initial_succeeded", "oe_structured_succeeded"),
        ("oe_structured_succeeded", "oe_initial_succeeded"),
    ],
)
def test_known_oe_terminal_families_cannot_alias_other_configuration(
    monkeypatch, actual, wrong
):
    factory, fence, run, attempt, *_, commits = arranged(monkeypatch, actual)
    with (
        pytest.raises(DomainError),
        lifecycle.neutral_transaction(factory, fence) as session,
    ):
        stage(session, wrong, run, attempt)
    assert not commits
