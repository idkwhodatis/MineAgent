"""Real flock + SQLAlchemy transaction boundaries, explicit domain-row doubles."""

import fcntl
import os
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, String, event, select
from sqlalchemy.orm import DeclarativeBase

from backend.app import openevolve_evaluation_admission as s
from backend.app import openevolve_lock_admission as window
from backend.app.research_object_services import object_storage_guard
from backend.app.services import DomainError
from tests.research_runtime.test_neutral_terminal_commit import arranged


class Base(DeclarativeBase):
    pass


class Row(Base):
    __tablename__ = "capacity_test_writes"
    id = Column(String, primary_key=True)
    amount = Column(Integer)


def fixture(monkeypatch, tmp_path):
    factory, fence, _run, attempt, *_, commits = arranged(
        monkeypatch, "oe_initial_succeeded"
    )
    Base.metadata.create_all(factory.kw["bind"])
    (tmp_path / "research-objects").mkdir()
    settings = NS(
        storage_root=tmp_path,
        neutral_object_owner_max_bytes=100,
        neutral_object_installation_max_bytes=200,
        neutral_object_max_files=1000,
    )
    candidate = NS(id=uuid4(), epoch_id=uuid4(), source_sha256="a" * 64)
    bootstrap, development = NS(spec=NS(execution=object())), NS(id="development")
    budget = NS(used={}, reserved={}, limits={})
    seen, writes, rollbacks = [], [], []
    monkeypatch.setattr(
        s, "_read_preparation", lambda *a: (candidate, bootstrap, development, None)
    )
    monkeypatch.setattr(s, "document_digest", lambda _: "b" * 64)
    monkeypatch.setattr(
        s, "SearchEvaluationPreparation", lambda **kw: Row(id=str(kw["id"]), amount=0)
    )

    def preflight(session, _fence, **kwargs):
        seen.append(session)
        with object_storage_guard(settings.storage_root):
            return budget, None, s.FLOOR

    def write(session, _fence, **kwargs):
        writes.append(kwargs["logical_key"])
        row = Row(id="reservation", amount=1)
        session.add(row)
        session.flush()
        return NS(id=uuid4())

    monkeypatch.setattr(s, "_budget_reservation_preflight", preflight)
    monkeypatch.setattr(s, "_write_budget_reservation", write)
    event.listen(factory.class_, "after_rollback", lambda _: rollbacks.append(True))
    return NS(
        factory=factory,
        fence=fence,
        attempt=attempt,
        settings=settings,
        seen=seen,
        writes=writes,
        commits=commits,
        rollbacks=rollbacks,
        budget=budget,
    )


def execute(c):
    return s.prepare_evaluation(
        c.factory,
        c.fence,
        None,
        event=None,
        ticket_key=b"k" * 32,
        window_id="development",
        settings=c.settings,
    )


@contextmanager
def held(root):
    fd = os.open(root / "research-objects", os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield fd
    finally:
        os.close(fd)


def test_actual_flock_collision_rolls_back_before_wait_and_reserves_once(
    monkeypatch, tmp_path
):
    c = fixture(monkeypatch, tmp_path)
    with held(tmp_path) as fd:

        def sleep(_):
            assert c.rollbacks == [True] and not c.seen[-1].in_transaction()
            assert c.writes == [] and c.commits == []
            fcntl.flock(fd, fcntl.LOCK_UN)

        monkeypatch.setattr(s.time, "sleep", sleep)
        with window.acquisition_window(c.fence, s.time.monotonic() + 5):
            execute(c)
    assert len(c.seen) == 2 and c.seen[0] is not c.seen[1]
    assert len(c.writes) == 1 and c.commits == [True]
    with c.factory.kw["bind"].connect() as connection:
        rows = connection.execute(select(Row.amount)).scalars().all()
    assert sorted(rows) == [0, 1]


def test_retry_rereads_revoked_fence(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    with held(tmp_path) as fd:

        def sleep(_):
            assert not c.seen[-1].in_transaction()
            c.attempt.lease_token = uuid4()
            fcntl.flock(fd, fcntl.LOCK_UN)

        monkeypatch.setattr(s.time, "sleep", sleep)
        with (
            window.acquisition_window(c.fence, s.time.monotonic() + 5),
            pytest.raises(DomainError),
        ):
            execute(c)
    assert not c.writes and not c.commits


@pytest.mark.parametrize("scope", [None, 0.12, 20.0])
def test_original_deadline_and_no_window_remain_bounded(monkeypatch, tmp_path, scope):
    c = fixture(monkeypatch, tmp_path)
    clock = [10.0]
    monkeypatch.setattr(s.time, "monotonic", lambda: clock[0])

    def sleep(delay):
        assert not c.seen[-1].in_transaction()
        clock[0] += delay

    monkeypatch.setattr(s.time, "sleep", sleep)
    from contextlib import nullcontext

    with (
        held(tmp_path),
        window.acquisition_window(c.fence, 10 + scope) if scope else nullcontext(),
        pytest.raises(DomainError),
    ):
        execute(c)
    assert clock[0] == pytest.approx(10 + (min(scope, 5) if scope else 0))
    assert not c.writes and not c.commits


@pytest.mark.parametrize("stage", ["write", "flush", "commit", "unknown", "cancel"])
def test_nonacquisition_errors_never_retry(monkeypatch, tmp_path, stage):
    c = fixture(monkeypatch, tmp_path)
    if stage in {"write", "flush"}:

        def badwrite(session, *_a, **_kw):
            c.writes.append("once")
            if stage == "flush":
                session.add(Row(id="side-effect", amount=1))
                session.flush()
            raise DomainError("neutral_object_busy", "busy", 409)

        monkeypatch.setattr(s, "_write_budget_reservation", badwrite)
    elif stage == "commit":

        def fail(_):
            raise DomainError("neutral_object_busy", "busy", 409)

        event.listen(c.factory.class_, "before_commit", fail)
    else:

        def fail(*_a, **_kw):
            c.seen.append(True)
            if stage == "cancel":
                raise KeyboardInterrupt
            raise DomainError("other", "unknown", 409)

        monkeypatch.setattr(s, "_budget_reservation_preflight", fail)
    with (
        window.acquisition_window(c.fence, s.time.monotonic() + 5),
        pytest.raises((DomainError, KeyboardInterrupt)),
    ):
        execute(c)
    assert len(c.seen) == 1 and not c.commits
    with c.factory.kw["bind"].connect() as connection:
        assert connection.execute(select(Row)).all() == []


@pytest.mark.parametrize(
    "kind",
    ["dirty", "flush", "dml", "driver", "dml_cte", "function", "sql_text", "literal"],
)
def test_preflight_cannot_write_then_be_retried(monkeypatch, tmp_path, kind):
    c = fixture(monkeypatch, tmp_path)

    def bad(session, *_a, **_kw):
        c.seen.append(session)
        if kind in {"function", "sql_text", "literal"}:
            from sqlalchemy import func, literal_column, text

            expression = {
                "function": func.some_writer(),
                "sql_text": text("some_writer()"),
                "literal": literal_column("some_writer()"),
            }[kind]
            session.execute(select(expression))
        elif kind == "dml_cte":
            session.execute(
                select(Row.id).add_cte(
                    Row.__table__.insert()
                    .values(id="bad", amount=1)
                    .returning(Row.id)
                    .cte()
                )
            )
        elif kind == "dml":
            session.execute(Row.__table__.insert().values(id="bad", amount=1))
        elif kind == "driver":
            session.connection().exec_driver_sql(
                "INSERT INTO capacity_test_writes VALUES ('bad',1)"
            )
        else:
            session.add(Row(id="bad", amount=1))
            if kind == "flush":
                session.flush()
        raise DomainError("neutral_object_busy", "busy", 409)

    monkeypatch.setattr(s, "_budget_reservation_preflight", bad)
    with (
        window.acquisition_window(c.fence, s.time.monotonic() + 5),
        pytest.raises((ValueError, DomainError)),
    ):
        execute(c)
    assert len(c.seen) == 1 and not c.writes and not c.commits


def admission(c):
    return s._preparation_admission(
        c.factory, c.fence, None, None, b"k" * 32, "development", c.settings, uuid4()
    )


@pytest.mark.parametrize(
    "change",
    [
        "root",
        "limit",
        "budget",
        "new",
        "dml",
        "driver",
        "closed",
        "floor",
        "transaction",
    ],
)
def test_admission_cannot_be_reused_after_mutation(monkeypatch, tmp_path, change):
    c = fixture(monkeypatch, tmp_path)
    with pytest.raises(ValueError), admission(c) as write:
        if change == "floor":
            from packages.research_runtime.budget_contracts import BudgetVector

            monkeypatch.setattr(s, "FLOOR", s.FLOOR.add(BudgetVector(storage_bytes=1)))
        elif change == "transaction":
            monkeypatch.setattr(c.seen[-1], "get_transaction", lambda: object())
        elif change == "root":
            c.settings.storage_root = tmp_path / "different"
        elif change == "limit":
            c.settings.neutral_object_max_files += 1
        elif change == "budget":
            c.budget.reserved = {"changed": True}
        elif change == "new":
            c.seen[-1].add(Row(id="new", amount=1))
        elif change == "dml":
            c.seen[-1].execute(Row.__table__.insert().values(id="bad", amount=1))
        elif change == "driver":
            c.seen[-1].connection().exec_driver_sql(
                "INSERT INTO capacity_test_writes VALUES ('bad',1)"
            )
        else:
            c.seen[-1].rollback()
        write()
    assert not c.writes and not c.commits


def test_writer_single_use_and_late_scope_rejected(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    with admission(c) as write:
        write()
        with pytest.raises(ValueError, match="consumed"):
            write()
    with pytest.raises(ValueError, match="consumed"):
        write()
    assert len(c.writes) == 1 and c.commits == [True]


def test_deadline_crossing_successful_acquisition_rolls_back_without_body(
    monkeypatch, tmp_path
):
    c = fixture(monkeypatch, tmp_path)
    clock = [10.0]
    monkeypatch.setattr(s.time, "monotonic", lambda: clock[0])
    original = s._budget_reservation_preflight

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 11.0
        return result

    monkeypatch.setattr(s, "_budget_reservation_preflight", slow)
    with window.acquisition_window(c.fence, 10.5), pytest.raises(DomainError):
        execute(c)
    assert not c.writes and not c.commits and c.rollbacks == [True]
    with object_storage_guard(tmp_path):
        pass


@pytest.mark.parametrize("new_physical_bytes", [False, True])
def test_actual_broker_quota_path_rechecks_capacity_before_single_budget_write(
    monkeypatch, tmp_path, new_physical_bytes
):
    from backend.app import openevolve_process_quota as quota
    from backend.app import openevolve_upload_quota as uploads
    from backend.app import rdagent_provider_quota as providers
    from backend.app import research_broker_services as broker
    from backend.app.core.config import Settings
    from backend.app.research_broker_models import (
        ResearchBudgetControl,
        ResearchBudgetReservation,
    )
    from backend.app.research_object_models import ResearchObjectPublication
    from packages.research_runtime.budget_contracts import BudgetVector

    c = fixture(monkeypatch, tmp_path)
    c.settings = Settings(
        storage_root=tmp_path,
        database_url="postgresql+psycopg://fixture:fixture@localhost/fixture",
        secret_key="local-test-only" * 4,
    )
    c.settings.neutral_object_owner_max_bytes = s.FLOOR.storage_bytes + 10000
    c.budget.used = BudgetVector().model_dump()
    c.budget.reserved = BudgetVector().model_dump()
    c.budget.limits = s.FLOOR.add(s.FLOOR).model_dump()
    original_scalar, original_scalars = (
        c.factory.class_.scalar,
        c.factory.class_.scalars,
    )
    original_add = c.factory.class_.add

    def scalar(session, statement, *args, **kwargs):
        model = statement.column_descriptions[0]["entity"]
        if model is ResearchBudgetControl:
            return c.budget
        if model is ResearchBudgetReservation:
            return None
        return original_scalar(session, statement, *args, **kwargs)

    def scalars(session, statement, *args, **kwargs):
        if statement.column_descriptions[0]["entity"] is ResearchObjectPublication:
            return NS(all=list)
        return original_scalars(session, statement, *args, **kwargs)

    def add(session, row, *args, **kwargs):
        if isinstance(row, ResearchBudgetReservation):
            c.writes.append(row.amounts)
            return original_add(session, Row(id=str(row.id), amount=1))
        return original_add(session, row, *args, **kwargs)

    monkeypatch.setattr(c.factory.class_, "scalar", scalar)
    monkeypatch.setattr(c.factory.class_, "scalars", scalars)
    monkeypatch.setattr(c.factory.class_, "add", add)
    monkeypatch.setattr(quota, "process_quota_required", lambda _: True)
    monkeypatch.setattr(quota, "process_holds", lambda _: ())
    monkeypatch.setattr(providers, "provider_process_holds", lambda _: ())
    monkeypatch.setattr(uploads, "upload_holds", lambda _: ())
    checks = []

    def preflight(*args, **kwargs):
        checks.append(args[0])
        return broker._budget_reservation_preflight(*args, **kwargs)

    monkeypatch.setattr(s, "_budget_reservation_preflight", preflight)
    monkeypatch.setattr(
        s, "_write_budget_reservation", broker._write_budget_reservation
    )
    with held(tmp_path) as fd:

        def sleep(_):
            assert c.budget.reserved == BudgetVector().model_dump()
            assert not checks[-1].in_transaction() and not c.writes
            if new_physical_bytes:
                directory = (
                    tmp_path
                    / "research-processes"
                    / str(c.fence.owner_id)
                    / str(uuid4())
                )
                directory.mkdir(parents=True)
                (directory / "retained.bin").write_bytes(b"x" * 11000)
            fcntl.flock(fd, fcntl.LOCK_UN)

        monkeypatch.setattr(s.time, "sleep", sleep)
        from contextlib import nullcontext

        with (
            window.acquisition_window(c.fence, s.time.monotonic() + 5),
            (
                pytest.raises(ValueError, match="capacity exhausted")
                if new_physical_bytes
                else nullcontext()
            ),
        ):
            execute(c)
    if new_physical_bytes:
        assert len(checks) == 2 and not c.writes and not c.commits
        assert c.budget.reserved == BudgetVector().model_dump()
        return
    assert len(checks) == 2 and c.writes == [s.FLOOR.model_dump()]
    assert c.budget.reserved == s.FLOOR.model_dump()
    assert c.commits == [True] and c.rollbacks == [True]


def test_caught_invalid_writer_does_not_enable_mutation_or_commit(
    monkeypatch, tmp_path
):
    c = fixture(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="did not complete"), admission(c) as write:
        c.settings.neutral_object_max_files += 1
        with pytest.raises(ValueError, match="accounting changed"):
            write()
        with pytest.raises(ValueError, match="before mutation"):
            c.seen[-1].execute(Row.__table__.insert().values(id="bad", amount=1))
    assert not c.commits and not c.writes


def test_cross_thread_writer_is_rejected_without_commit(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    c = fixture(monkeypatch, tmp_path)
    with (
        pytest.raises(ValueError, match="did not complete"),
        admission(c) as write,
        ThreadPoolExecutor(1) as pool,
        pytest.raises(ValueError, match="transaction differs"),
    ):
        pool.submit(write).result()
    assert not c.commits and not c.writes


def test_capacity_settings_changed_during_preflight_rejected(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    original = s._budget_reservation_preflight

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        c.settings.storage_root = tmp_path / "changed"
        return result

    monkeypatch.setattr(s, "_budget_reservation_preflight", changed)
    with pytest.raises(ValueError, match="accounting changed"):
        execute(c)
    assert not c.commits and not c.writes


def test_swallowed_postwrite_flush_error_cannot_commit_pending_mutation(
    monkeypatch, tmp_path
):
    c = fixture(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Mutation after"), admission(c) as write:
        write()
        c.seen[-1].add(Row(id="late", amount=1))
        with pytest.raises(ValueError, match="before mutation"):
            c.seen[-1].flush()
    assert not c.commits
    with c.factory.kw["bind"].connect() as connection:
        assert connection.execute(select(Row)).all() == []
