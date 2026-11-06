"""Dispatcher pagination with real SQL ordering and explicit queue/run doubles.

SQLite exercises selection only; PostgreSQL locking/atomic queue acceptance is
covered separately by tests/integration/test_outbox.py.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from backend.app.models import OutboxEvent
from backend.app.services import DomainError
from tests.research_runtime.fixtures import seed_config
from workers.research import dispatcher, oe_structured_queue, queue, rdagent_queue


@pytest.fixture
def case(monkeypatch):
    engine = create_engine("sqlite://")
    OutboxEvent.__table__.create(engine)
    runs = {}
    blocked = set()
    queries = []
    tasks = {
        name: MagicMock()
        for name in (
            queue.TASK_NAME,
            rdagent_queue.TASK_NAME,
            oe_structured_queue.TASK_NAME,
        )
    }
    for task in tasks.values():
        task.configure.return_value.defer.return_value = 42
    app = MagicMock(return_value=NS(tasks=tasks))
    monkeypatch.setattr(dispatcher, "create_queue_app", app)

    def validate(session, run, event, settings):
        app.assert_not_called()
        if run.id in blocked:
            code = (
                "engine_submission_not_enabled"
                if run.engine == "rdagent"
                else "oe_submission_disabled"
            )
            raise DomainError(code, "Disabled fixture engine", 409)

    monkeypatch.setattr(rdagent_queue, "require_dispatch_binding", validate)
    monkeypatch.setattr(oe_structured_queue, "require_dispatch_binding", validate)
    with Session(engine) as sql:
        session = MagicMock()
        session.bind.dialect.name = "postgresql"

        def scalars(query):
            queries.append(str(query.compile(dialect=postgresql.dialect())))
            return sql.scalars(query)

        session.scalars.side_effect = scalars
        session.get.side_effect = lambda model, identity: runs[identity]
        session.flush.side_effect = sql.flush

        def add(family, *, disabled=False, created_at=None):
            number = len(runs) + 1
            event = OutboxEvent(
                id=UUID(int=(0xA << 124) + number),
                run_id=UUID(int=(0xB << 124) + number),
                generation=1,
                created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
            )
            run = NS(
                id=event.run_id,
                purpose={
                    "legacy": "legacy_strategy",
                    "rd": "factor_research",
                    "oe": "strategy_search",
                }[family],
                engine={"legacy": "legacy", "rd": "rdagent", "oe": "openevolve"}[
                    family
                ],
                config={"schema_version": "mineagent.oe-structured-provider-run.v1"}
                if family == "oe"
                else seed_config()
                if family == "legacy"
                else {},
            )
            runs[run.id] = run
            if disabled:
                blocked.add(run.id)
            sql.add(event)
            sql.flush()
            return event

        yield NS(
            session=session,
            sql=sql,
            add=add,
            runs=runs,
            blocked=blocked,
            tasks=tasks,
            app=app,
            queries=queries,
        )
    engine.dispose()


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_disabled_backlog_larger_than_two_pages_does_not_starve_any_family(
    case, same_timestamp
):
    pending = [
        case.add(
            "rd" if i % 2 else "oe",
            disabled=True,
            created_at=datetime(2026, 1, 1, tzinfo=UTC)
            + timedelta(seconds=0 if same_timestamp else i),
        )
        for i in range(205)
    ]
    ready = [
        case.add(
            family,
            created_at=datetime(2026, 1, 2, tzinfo=UTC) if not same_timestamp else None,
        )
        for family in ("legacy", "rd", "oe")
    ]
    assert dispatcher.dispatch_outbox(case.session) == 3
    assert all(
        event.dispatched_at is None and event.job_id is None for event in pending
    )
    assert all(
        event.dispatched_at is not None and event.job_id == 42 for event in ready
    )
    assert len(case.queries) == 3
    assert all("FOR UPDATE SKIP LOCKED" in query for query in case.queries)
    assert "(outbox_events.created_at, outbox_events.id) >" in case.queries[1]
    for task, event in zip(case.tasks.values(), ready, strict=True):
        task.configure.return_value.defer.assert_called_once_with(
            run_id=str(event.run_id), generation=1
        )


def test_dispatch_limit_applies_to_eligible_events_and_disabled_rows_can_resume(case):
    pending = [case.add("rd", disabled=True) for _ in range(101)]
    ready = [case.add("legacy") for _ in range(105)]
    assert dispatcher.dispatch_outbox(case.session) == 100
    assert all(event.dispatched_at is not None for event in ready[:100])
    assert all(event.dispatched_at is None for event in [*pending, *ready[100:]])
    case.app.reset_mock()
    case.blocked.clear()
    assert dispatcher.dispatch_outbox(case.session) == 100
    assert all(event.dispatched_at is not None for event in pending[:100])
    assert pending[-1].dispatched_at is None
    case.app.reset_mock()
    assert dispatcher.dispatch_outbox(case.session) == 6
    case.app.reset_mock()
    assert dispatcher.dispatch_outbox(case.session) == 0
    case.app.assert_not_called()
    assert not case.sql.scalars(
        select(OutboxEvent).where(OutboxEvent.dispatched_at.is_(None))
    ).all()


def test_invalid_later_page_fails_before_any_queue_job_is_created(case):
    first = case.add("legacy")
    pending = [case.add("oe", disabled=True) for _ in range(101)]
    invalid = case.add("legacy")
    case.runs[invalid.run_id].engine = "unknown"
    with pytest.raises(DomainError):
        dispatcher.dispatch_outbox(case.session)
    case.app.assert_not_called()
    case.session.connection.assert_not_called()
    assert all(event.dispatched_at is None for event in [first, *pending, invalid])


def test_all_disabled_full_pages_terminate_without_consuming_events(case):
    pending = [case.add("oe", disabled=True) for _ in range(200)]
    assert dispatcher.dispatch_outbox(case.session) == 0
    assert len(case.queries) == 3
    assert all(event.dispatched_at is None for event in pending)
    case.app.assert_not_called()
