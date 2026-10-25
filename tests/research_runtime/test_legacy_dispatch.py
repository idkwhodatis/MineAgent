"""Fail before queue, codec, filesystem or containment side effects."""

import secrets
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.app.core.config import Settings
from backend.app.services import DomainError, claim_attempt, reconcile_attempt
from packages.research_runtime.identity import RunIdentityError
from workers.research.executor import execute, prepare_input, stage_input


def settings(**changes):
    return Settings(
        database_url="postgresql://localhost/not_used",
        secret_key=secrets.token_urlsafe(48),
        **changes,
    )


def unsupported_run(**changes):
    values = {
        "id": uuid4(),
        "purpose": "factor_research",
        "engine": "rdagent",
        "config": {"schema_version": "mineagent.factor-research.v1"},
        "generation": 1,
        "status": "queued",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_unsupported_claim_has_no_attempt_or_legacy_sandbox():
    session = MagicMock()
    run = unsupported_run()
    session.scalar.return_value = run
    with pytest.raises(DomainError, match="unsupported"):
        claim_attempt(session, run.id, 1, "test", settings())
    session.add.assert_not_called()
    session.flush.assert_not_called()
    assert session.scalar.call_count == 1


def test_executor_missing_run_is_stale_without_runner_import():
    session = MagicMock()
    session.get.return_value = None
    assert execute.__wrapped__(
        str(uuid4()), 1, settings(), lambda: nullcontext(session)
    ) == {"status": "duplicate_or_stale"}


def test_executor_rejects_identity_before_dispatch():
    session = MagicMock()
    session.get.return_value = unsupported_run()
    with pytest.raises(RunIdentityError):
        execute.__wrapped__(str(uuid4()), 1, settings(), lambda: nullcontext(session))


def test_input_preparation_never_reads_references_or_writes_for_unknown_run(tmp_path):
    session = MagicMock()
    run = unsupported_run()
    with pytest.raises(RunIdentityError):
        prepare_input(session, run, uuid4(), settings(storage_root=tmp_path))
    session.get.assert_not_called()
    with pytest.raises(RunIdentityError):
        stage_input(run, None, None, uuid4(), settings(storage_root=tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_reconciliation_never_stops_unknown_process_family():
    from datetime import timedelta

    from backend.app.models import utcnow

    attempt = SimpleNamespace(
        id=uuid4(), finished_at=None, lease_expires_at=utcnow() - timedelta(seconds=30)
    )
    run = unsupported_run(active_attempt_id=attempt.id)
    session = MagicMock()
    session.scalar.side_effect = [run.id, run, attempt]
    stop = MagicMock()
    with pytest.raises(DomainError, match="unsupported"):
        reconcile_attempt(session, attempt.id, stop)
    stop.assert_not_called()
    session.add.assert_not_called()


def test_outbox_checks_all_identities_before_first_enqueue():
    from workers.research.dispatcher import dispatch_outbox

    session = MagicMock()
    session.bind.dialect.name = "postgresql"
    session.scalars.return_value.all.return_value = [SimpleNamespace(run_id=uuid4())]
    session.get.return_value = unsupported_run()
    with pytest.raises(DomainError, match="unsupported"):
        dispatch_outbox(session)
    session.connection.assert_not_called()
    session.flush.assert_not_called()


def test_stalled_job_recovery_rejects_unknown_identity_before_retry():
    from workers.research.dispatcher import recover_stalled_jobs

    session = MagicMock()
    identifier = uuid4()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"id": 12, "args": {"run_id": str(identifier), "generation": 1}}
    ]
    session.scalars.return_value.one_or_none.return_value = unsupported_run(
        id=identifier
    )
    with pytest.raises(DomainError, match="unsupported"):
        recover_stalled_jobs(session)
    assert session.execute.call_count == 1
    assert "retry_job" not in str(session.execute.call_args.args[0])


@pytest.mark.parametrize(
    "args",
    [
        None,
        {},
        {"run_id": "bad", "generation": 1},
        {"run_id": str(uuid4()), "generation": True},
        {"run_id": str(uuid4()), "generation": 1, "engine": "rdagent"},
    ],
)
def test_stalled_job_recovery_requires_exact_queue_binding(args):
    from workers.research.dispatcher import recover_stalled_jobs

    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"id": 12, "args": args}
    ]
    with pytest.raises(DomainError, match="exact run binding"):
        recover_stalled_jobs(session)
    assert session.execute.call_count == 1
    session.scalars.assert_not_called()


def test_stalled_job_recovery_checks_all_before_retrying_one():
    from tests.research_runtime.fixtures import seed_config
    from workers.research.dispatcher import recover_stalled_jobs

    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"id": 12, "args": {"run_id": str(uuid4()), "generation": 1}},
        {"id": 13, "args": {"run_id": str(uuid4()), "generation": 1}},
    ]
    session.scalars.return_value.one_or_none.side_effect = [
        SimpleNamespace(
            purpose="legacy_strategy", engine="legacy", config=seed_config()
        ),
        None,
    ]
    with pytest.raises(DomainError):
        recover_stalled_jobs(session)
    assert session.execute.call_count == 1


def test_stalled_job_exact_legacy_binding_preserves_retry_protocol():
    from sqlalchemy.dialects import postgresql

    from tests.research_runtime.fixtures import seed_config
    from workers.research.dispatcher import recover_stalled_jobs

    identifier = uuid4()
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"id": 12, "args": {"run_id": str(identifier), "generation": 3}}
    ]
    session.scalars.return_value.one_or_none.return_value = SimpleNamespace(
        purpose="legacy_strategy", engine="legacy", config=seed_config()
    )
    assert recover_stalled_jobs(session) == 1
    query = str(
        session.scalars.call_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "outbox_events.job_id = 12" in query
    assert str(identifier) in query
    assert "outbox_events.generation = 3" in query
    assert "procrastinate_retry_job_v2" in str(session.execute.call_args.args[0])
    assert session.execute.call_args.args[1] == {"job_id": 12}
