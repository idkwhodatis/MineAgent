"""Actual file locks and rollback ordering; no publication/body retries."""
# ruff: noqa: SIM117

import fcntl
import os
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.app import openevolve_lock_admission as window
from backend.app import openevolve_object_publication as publication
from backend.app import research_object_services as objects
from backend.app.services import DomainError
from tests.openevolve.test_operational_lock_admission import fence


def storage(tmp_path):
    (tmp_path / "research-objects").mkdir()
    fd = os.open(tmp_path / "research-objects", os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_real_filesystem_acquisition_waits_before_body_and_never_replays(
    tmp_path, monkeypatch
):
    fd, f, waits, bodies = storage(tmp_path), fence(), [], []

    def sleep(_):
        assert not bodies
        waits.append(True)
        fcntl.flock(fd, fcntl.LOCK_UN)

    monkeypatch.setattr(window.time, "sleep", sleep)
    try:
        with pytest.raises(DomainError) as error:
            with window.publication_storage_admission(tmp_path, f):
                pytest.fail("default must remain NOWAIT")
        assert error.value.code == "neutral_object_busy" and not waits
        with window.acquisition_window(f, time.monotonic() + 5):
            with pytest.raises(DomainError):
                with window.publication_storage_admission(tmp_path, f):
                    bodies.append(True)
                    raise objects._error("neutral_object_busy")
        assert waits == bodies == [True]
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_reservation_rolls_back_real_transaction_before_filesystem_wait(
    tmp_path, monkeypatch
):
    fd, f, sessions, waits = storage(tmp_path), fence(), [], []
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE reservations (value INTEGER)"))

    @contextmanager
    def transaction(*args, **kwargs):
        with Session(engine) as session, session.begin():
            sessions.append(session)
            # This represents the uncommitted budget reservation before the FS
            # attempt. It must not survive failed acquisition or be double-counted.
            session.execute(text("INSERT INTO reservations VALUES (1)"))
            yield SimpleNamespace(scalar=lambda _: None)

    monkeypatch.setattr(publication, "neutral_transaction", transaction)
    monkeypatch.setattr(publication, "_current_search", lambda *_: None)
    monkeypatch.setattr(publication, "require_search_identity", lambda *_: None)
    monkeypatch.setattr(
        publication, "reserve_budget", lambda *a, **k: SimpleNamespace(id=uuid4())
    )

    def reserve(*args):
        with objects.object_storage_guard(tmp_path):
            return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(publication, "reserve_object", reserve)

    def sleep(_):
        assert not sessions[-1].in_transaction()
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM reservations")) == 0
        waits.append(True)
        fcntl.flock(fd, fcntl.LOCK_UN)

    monkeypatch.setattr(window.time, "sleep", sleep)
    try:
        with window.acquisition_window(f, time.monotonic() + 5):
            result = window.retry_acquisition(
                lambda: publication._reserve(
                    None,
                    f,
                    "model_request",
                    b"{}",
                    "key",
                    SimpleNamespace(storage_root=tmp_path),
                    "sha",
                ),
                f,
                publication._ReservationBusy,
            )
        assert result and waits == [True] and len(sessions) == 2
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM reservations")) == 1
    finally:
        os.close(fd)
        engine.dispose()


@pytest.mark.parametrize("where", ["reserve_other", "commit"])
def test_reservation_non_acquisition_errors_never_retry(monkeypatch, where):
    f, calls = fence(), []

    @contextmanager
    def transaction(*args, **kwargs):
        calls.append("enter")
        yield SimpleNamespace(scalar=lambda _: None)
        raise objects._error("neutral_object_busy")

    monkeypatch.setattr(publication, "neutral_transaction", transaction)
    monkeypatch.setattr(publication, "_current_search", lambda *_: None)
    monkeypatch.setattr(publication, "require_search_identity", lambda *_: None)
    monkeypatch.setattr(
        publication, "reserve_budget", lambda *a, **k: SimpleNamespace(id=uuid4())
    )

    def reserve(*args):
        if where == "reserve_other":
            raise objects._error("neutral_object_capacity")
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(publication, "reserve_object", reserve)
    with window.acquisition_window(f, time.monotonic() + 5), pytest.raises(DomainError):
        window.retry_acquisition(
            lambda: publication._reserve(
                None, f, "model_request", b"{}", "key", None, "sha"
            ),
            f,
            publication._ReservationBusy,
        )
    assert calls == ["enter"]


def test_public_entry_preserves_busy_error_and_does_not_retry_publication(monkeypatch):
    f, calls = fence(), []

    def reserve(*args):
        calls.append("reserve")
        raise publication._ReservationBusy from objects._error("neutral_object_busy")

    monkeypatch.setattr(publication, "_reserve", reserve)
    with pytest.raises(DomainError) as error:
        publication.publish_search_object(
            None,
            f,
            kind="model_request",
            content=b"{}",
            logical_key="key",
            settings=None,
        )
    assert error.value.code == "neutral_object_busy" and calls == ["reserve"]
    monkeypatch.setattr(publication, "_reserve", lambda *a: uuid4())

    def publish(*args):
        calls.append("publish")
        raise objects._error("neutral_object_busy")

    monkeypatch.setattr(publication, "publish_reserved_object", publish)
    with window.acquisition_window(f, time.monotonic() + 5), pytest.raises(DomainError):
        publication.publish_search_object(
            None,
            f,
            kind="model_request",
            content=b"{}",
            logical_key="key",
            settings=None,
        )
    assert calls == ["reserve", "publish"]


def test_storage_deadline_and_foreign_fence_never_enter(tmp_path, monkeypatch):
    fd, f, clock = storage(tmp_path), fence(), [10.0]
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        window.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    try:
        for original, expected in ((11.0, 11.0), (100.0, 15.0)):
            clock[0] = 10.0
            with window.acquisition_window(f, original), pytest.raises(DomainError):
                with window.publication_storage_admission(tmp_path, f):
                    pytest.fail("held lock must never yield")
            assert clock[0] == pytest.approx(expected)
        with window.acquisition_window(f, 100), pytest.raises(DomainError):
            with window.publication_storage_admission(tmp_path, fence()):
                pytest.fail("foreign fence")
    finally:
        os.close(fd)


def test_late_filesystem_acquisition_releases_guard_without_body(tmp_path, monkeypatch):
    f, clock, calls = fence(), [10.0], []

    @contextmanager
    def guard(root):
        calls.append("enter")
        clock[0] = 20.0
        try:
            yield
        finally:
            calls.append("closed")

    monkeypatch.setattr(objects, "object_storage_guard", guard)
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])
    with window.acquisition_window(f, 15), pytest.raises(DomainError):
        with window.publication_storage_admission(tmp_path, f):
            pytest.fail("late acquisition")
    assert calls == ["enter", "closed"]


def test_successful_reservation_late_commit_is_not_replayed(monkeypatch):
    f, clock, calls = fence(), [10.0], []
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])

    def reserve(*args):
        calls.append("committed")
        clock[0] = 20.0
        return uuid4()

    monkeypatch.setattr(publication, "_reserve", reserve)
    with window.acquisition_window(f, 15), pytest.raises(DomainError):
        publication.publish_search_object(
            None,
            f,
            kind="model_request",
            content=b"{}",
            logical_key="key",
            settings=None,
        )
    assert calls == ["committed"]


def test_copied_context_cannot_acquire_filesystem_in_other_thread(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    f, entered = fence(), []

    @contextmanager
    def guard(root):
        entered.append(True)
        yield

    def acquire():
        with window.publication_storage_admission(tmp_path, f):
            pytest.fail("cross thread")

    monkeypatch.setattr(objects, "object_storage_guard", guard)
    with window.acquisition_window(f, time.monotonic() + 5):
        context = copy_context()
        with ThreadPoolExecutor(1) as pool, pytest.raises(DomainError):
            pool.submit(context.run, acquire).result()
    assert not entered


def test_nested_db_admission_cannot_restart_five_second_window(monkeypatch):
    from backend.app import research_lifecycle as lifecycle

    f, clock, deadlines = fence(), [10.0], []
    monkeypatch.setattr(window.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        window.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )

    @contextmanager
    def transaction(*args, **kwargs):
        deadlines.append(window.current_window(f).deadline)
        raise lifecycle._neutral_error("neutral_busy")
        yield

    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", transaction)

    def acquire():
        clock[0] = 14.9
        with lifecycle.neutral_transaction(None, f):
            pytest.fail("DB lock busy")

    with window.acquisition_window(f, 100), pytest.raises(DomainError):
        window.retry_acquisition(acquire, f, publication._ReservationBusy)
    assert clock[0] == pytest.approx(15.0) and set(deadlines) == {15.0}
