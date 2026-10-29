"""Real SQLAlchemy transaction/commit hooks; row/physical closure explicitly doubled."""

from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import research_lifecycle as lifecycle
from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence
from workers.research import oe_structured_finish as finish


def arranged(monkeypatch):
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    run = NS(
        id=fence.run_id,
        owner_id=fence.owner_id,
        active_attempt_id=fence.attempt_id,
        generation=1,
        status="running",
        purpose="strategy_search",
        engine="openevolve",
        config={"schema_version": "mineagent.oe-structured-provider-run.v1"},
    )
    attempt = NS(
        id=fence.attempt_id,
        run_id=fence.run_id,
        generation=1,
        lease_token=fence.lease_token,
        lease_expires_at=utcnow() + timedelta(seconds=60),
        status="running",
        finished_at=None,
    )

    class Rows(Session):
        def scalar(self, statement, *a, **kw):
            entity = statement.column_descriptions[0]["entity"]
            return {ResearchRun: run, ExecutionAttempt: attempt}[entity]

        def scalars(self, statement, *a, **kw):
            return NS(all=lambda: ["028_oe_structured_search"])

    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine, class_=Rows)
    monkeypatch.setattr(lifecycle, "_try_neutral_lock", lambda *a: None)
    from backend.app import oe_structured_identity

    monkeypatch.setattr(oe_structured_identity, "require_run_identity", lambda *a: None)
    monkeypatch.setattr(finish, "_validate_current_success", lambda *a: uuid4())
    return factory, fence, run, attempt


def test_exact_terminal_survives_real_commit_hook_but_never_grants_more_work(
    monkeypatch,
):
    factory, fence, run, attempt = arranged(monkeypatch)
    with lifecycle.neutral_transaction(
        factory, fence, resource_scope="factor_numeric"
    ) as session:
        result = lifecycle.finish_oe_structured_transaction(
            session, fence, uuid4(), object()
        )
        with pytest.raises(DomainError):
            lifecycle.require_neutral_transaction(session, fence)
    assert result is not None
    assert run.status == attempt.status == "succeeded"
    assert attempt.finished_at is not None


@pytest.mark.parametrize(
    "defect", ["unstaged", "lease", "status", "stop", "exit", "finished"]
)
def test_real_commit_hook_still_vetoes_unproven_or_drifted_terminal(
    monkeypatch, defect
):
    factory, fence, run, attempt = arranged(monkeypatch)
    with pytest.raises(DomainError):  # noqa: SIM117 - distinguish expected veto and transaction
        with lifecycle.neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            if defect == "unstaged":
                run.status = attempt.status = "succeeded"
                attempt.finished_at = utcnow()
            else:
                lifecycle.finish_oe_structured_transaction(
                    session, fence, uuid4(), object()
                )
                if defect == "lease":
                    attempt.lease_expires_at = utcnow() - timedelta(seconds=1)
                elif defect == "status":
                    run.status = "cancel_requested"
                elif defect == "stop":
                    attempt.sandbox_stopped = False
                elif defect == "exit":
                    attempt.exit_code = 0
                elif defect == "finished":
                    attempt.finished_at = utcnow() + timedelta(seconds=1)
