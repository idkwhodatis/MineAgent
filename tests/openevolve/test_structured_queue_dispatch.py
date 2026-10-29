"""Actual queue registration/dispatcher with explicit transactional doubles."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.app.services import DomainError
from workers.research import dispatcher, oe_structured_queue, queue


def setup(monkeypatch):
    event = NS(
        id=uuid4(), run_id=uuid4(), generation=1, job_id=None, dispatched_at=None
    )
    run = NS(
        purpose="strategy_search",
        engine="openevolve",
        config={"schema_version": "mineagent.oe-structured-provider-run.v1"},
    )
    session = MagicMock()
    session.bind.dialect.name = "postgresql"
    session.scalars.return_value.all.return_value = [event]
    session.get.return_value = run
    task = MagicMock()
    task.configure.return_value.defer.return_value = 42
    app = NS(tasks={oe_structured_queue.TASK_NAME: task})
    factory = MagicMock(return_value=app)
    monkeypatch.setattr(dispatcher, "create_queue_app", factory)
    return session, event, run, task, factory


def test_normal_receipt_validated_before_transactional_enqueue(monkeypatch):
    session, event, _, task, factory = setup(monkeypatch)

    def validate(*args):
        factory.assert_not_called()
        assert args[0] is session

    monkeypatch.setattr(oe_structured_queue, "require_dispatch_binding", validate)
    assert dispatcher.dispatch_outbox(session) == 1
    assert event.job_id == 42 and event.dispatched_at is not None
    task.configure.return_value.defer.assert_called_once_with(
        run_id=str(event.run_id), generation=1
    )


@pytest.mark.parametrize(
    "code,pending",
    [
        ("oe_submission_disabled", True),
        ("oe_model_unavailable", True),
        ("runtime_schema_unavailable", False),
        ("oe_structured_queue_binding", False),
    ],
)
def test_disabled_pending_but_corruption_raises(monkeypatch, code, pending):
    session, event, _, _, factory = setup(monkeypatch)

    def reject(*a):
        raise DomainError(code, "test", 409)

    monkeypatch.setattr(oe_structured_queue, "require_dispatch_binding", reject)
    if pending:
        assert dispatcher.dispatch_outbox(session) == 0
    else:
        with pytest.raises(DomainError):
            dispatcher.dispatch_outbox(session)
    assert event.job_id is None and event.dispatched_at is None
    factory.assert_not_called()


def test_old_python_profile_never_enters_normal_dispatch(monkeypatch):
    session, _, run, _, factory = setup(monkeypatch)
    run.config["schema_version"] = "mineagent.openevolve-source.v1"
    validate = MagicMock()
    monkeypatch.setattr(oe_structured_queue, "require_dispatch_binding", validate)
    with pytest.raises((DomainError, ValueError)):
        dispatcher.dispatch_outbox(session)
    validate.assert_not_called()
    factory.assert_not_called()


def test_normal_task_registered_separately_without_execution():
    app = queue.create_queue_app(database_url="postgresql://localhost/not_used")
    assert oe_structured_queue.TASK_NAME in app.tasks
    assert queue.TASK_NAME in app.tasks
    assert "mineagent.execute_oe_structured_run" != queue.TASK_NAME
