"""Offline SQL/ORM pagination regression; not PostgreSQL concurrency evidence."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, Uuid, create_engine, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from backend.app import research_routes as routes
from backend.app.models import Evaluation, ModelCall, ResearchCheckpoint
from backend.app.services import DomainError


@pytest.fixture(params=[Evaluation, ModelCall, ResearchCheckpoint])
def page_store(request, monkeypatch):
    model = request.param
    engine = create_engine("sqlite://")
    # Deliberately minimal storage: exercise the real mapped projections, not
    # PostgreSQL constraints or native workflow admission.
    table = Table(
        model.__tablename__,
        MetaData(),
        *[
            Column(
                c.name,
                JSON()
                if isinstance(c.type, JSONB)
                else Uuid()
                if isinstance(c.type, PGUUID)
                else c.type,
                primary_key=c.primary_key,
                nullable=not c.primary_key,
            )
            for c in model.__table__.columns
        ],
    )
    table.create(engine)
    run_id, other_run, owner = UUID(int=10), UUID(int=11), UUID(int=12)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        {"id": UUID(int=i), "run_id": run_id, "created_at": now} for i in (3, 1, 2)
    ]
    records.append({"id": UUID(int=4), "run_id": other_run, "created_at": now})
    if model is ResearchCheckpoint:
        for record in records:
            record["sequence"] = 1 if record["id"].int == 3 else 2
    with engine.begin() as connection:
        connection.execute(table.insert(), records)

    def owned(session, requested_run, requested_owner):
        if (requested_run, requested_owner) != (run_id, owner):
            raise DomainError("not_found", "Research run not found", 404)

    monkeypatch.setattr(routes, "owned_research_run", owned)
    with Session(engine) as session:
        yield model, engine, session, run_id, owner
    engine.dispose()


@pytest.mark.parametrize("offset,limit", [(0, 2), (2, 2), (3, 2), (100, 2)])
def test_page_and_total_use_one_statement_with_stable_order(page_store, offset, limit):
    model, engine, session, run_id, owner = page_store
    statements = []
    event.listen(
        engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    compiled = []
    event.listen(
        session,
        "do_orm_execute",
        lambda state: compiled.append(
            state.statement.compile(dialect=postgresql.dialect())
        ),
    )
    page = routes._page(session, model, run_id, owner, offset, limit)
    expected = [3, 1, 2] if model is ResearchCheckpoint else [1, 2, 3]
    assert [item.id.int for item in page["data"]] == expected[offset : offset + limit]
    assert page["count"] == 3
    assert len(statements) == 1  # A second count query would reopen the race.
    assert "LEFT OUTER JOIN" in statements[0]
    assert len(compiled) == 1
    sql = str(compiled[0])
    assert "LEFT OUTER JOIN" in sql and " ON true" in sql
    assert sql.count("ORDER BY") == 2
    assert " LIMIT " in sql and " OFFSET " in sql


def test_empty_run_retains_zero_count(page_store):
    model, _engine, session, run_id, owner = page_store
    session.execute(model.__table__.delete())
    assert routes._page(session, model, run_id, owner, 0, 50) == {
        "data": [],
        "count": 0,
    }


def test_owner_check_precedes_any_page_query(page_store):
    model, engine, session, run_id, _owner = page_store
    statements = []
    event.listen(
        engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    with pytest.raises(DomainError):
        routes._page(session, model, run_id, UUID(int=99), 0, 50)
    assert statements == []
