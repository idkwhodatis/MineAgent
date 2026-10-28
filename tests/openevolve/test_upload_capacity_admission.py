"""Real flock acquisition; explicit DB/authority doubles, no hosted acceptance."""

# Keep failure boundaries explicit in admission/transaction tests.
# ruff: noqa: SIM117
import fcntl
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_lock_admission as locks
from backend.app import openevolve_process_quota
from backend.app import openevolve_upload_quota as quota
from backend.app import openevolve_upload_services as service
from backend.app.research_object_services import object_storage_guard
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence
from tests.openevolve.test_operational_upload import setup


def actual_lock(root):
    fd = os.open(root / "research-objects", os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def held(root):
    with pytest.raises(DomainError) as exc, object_storage_guard(root):
        pytest.fail("exclusive fence not retained")
    assert exc.value.code == "neutral_object_busy"


def arrange(monkeypatch, tmp_path, failure=None):
    admission = quota.upload_transaction
    capacity = quota.require_upload_capacity
    old = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(quota, "upload_transaction", admission)
    monkeypatch.setattr(quota, "require_upload_capacity", capacity)
    (tmp_path / "research-objects").mkdir()
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    calls = []
    settings = NS(
        storage_root=tmp_path,
        neutral_object_max_files=10000,
        neutral_object_installation_max_bytes=10**8,
        neutral_object_owner_max_bytes=10**8,
    )
    transaction_id = NS(is_active=True)

    class Session:
        def get_transaction(self):
            return transaction_id

        def scalars(self, _):
            return NS(all=list)

        def add(self, _):
            calls.append("add")

        def flush(self):
            calls.append("flush")

    @contextmanager
    def transaction(*_, **kw):
        held(tmp_path)
        calls.append("fresh_tx")
        try:
            yield Session()
            held(tmp_path)
            calls.append("commit")
            if failure == "commit":
                raise DomainError("neutral_object_busy", "fixed", 409)
        finally:
            calls.append("tx_closed")

    def authority(*_):
        calls.append("authority")
        if failure == "authority":
            raise ValueError("lost peer")
        return NS(), None

    def reserve(*_, **kw):
        held(tmp_path)
        calls.append("reserve")
        if failure == "body":
            raise DomainError("neutral_object_busy", "fixed", 409)
        if failure == "cancel":
            raise KeyboardInterrupt
        return NS(id=uuid4())

    monkeypatch.setattr(service, "neutral_transaction", transaction)
    monkeypatch.setattr(quota, "neutral_transaction", transaction)
    monkeypatch.setattr(service, "_upload", authority)
    monkeypatch.setattr(service, "_current", lambda *_: None)
    monkeypatch.setattr(service, "reserve_budget", reserve)
    monkeypatch.setattr(openevolve_process_quota, "combined_holds", lambda *_: ())

    def run():
        return service.begin_upload(
            None,
            fence,
            object(),
            epoch_id=old.epoch,
            payload=old.begin,
            settings=settings,
        )

    return NS(run=run, calls=calls, fence=fence, settings=settings)


def test_real_contention_releases_before_fresh_authority_and_holds_through_commit(
    monkeypatch, tmp_path
):
    c = arrange(monkeypatch, tmp_path)
    fd = actual_lock(tmp_path)
    sleeps = []

    def release(delay):
        assert c.calls == []
        sleeps.append(delay)
        os.close(fd)

    monkeypatch.setattr(locks.time, "sleep", release)
    with locks.acquisition_window(c.fence, locks.time.monotonic() + 2):
        assert c.run()["reserved"] is True
    assert len(sleeps) == 1
    assert c.calls == [
        "fresh_tx",
        "authority",
        "reserve",
        "add",
        "flush",
        "commit",
        "tx_closed",
    ]
    with object_storage_guard(tmp_path):
        pass
    assert quota._admission.get() is None


@pytest.mark.parametrize("failure", ["body", "commit", "cancel", "authority"])
def test_body_commit_cancel_and_stale_authority_never_replayed(
    monkeypatch, tmp_path, failure
):
    c = arrange(monkeypatch, tmp_path, failure)
    with locks.acquisition_window(c.fence, locks.time.monotonic() + 2):
        with pytest.raises((DomainError, ValueError, KeyboardInterrupt)):
            c.run()
    assert c.calls.count("fresh_tx") == 1
    assert c.calls.count("authority") == 1
    assert c.calls.count("reserve") == (0 if failure == "authority" else 1)
    with object_storage_guard(tmp_path):
        pass
    assert quota._admission.get() is None


@pytest.mark.parametrize("window", [None, 0.02])
def test_busy_without_window_or_expired_window_never_enters_db(
    monkeypatch, tmp_path, window
):
    c = arrange(monkeypatch, tmp_path)
    fd = actual_lock(tmp_path)
    try:
        if window is None:
            with pytest.raises(DomainError):
                c.run()
        else:
            with locks.acquisition_window(c.fence, locks.time.monotonic() + window):
                with pytest.raises(DomainError):
                    c.run()
        assert c.calls == []
    finally:
        os.close(fd)
    with object_storage_guard(tmp_path):
        pass


def test_quota_still_rejects_and_scope_cannot_be_transferred(monkeypatch, tmp_path):
    c = arrange(monkeypatch, tmp_path)
    c.settings.neutral_object_max_files = 0
    with pytest.raises(ValueError, match="quota exhausted"):
        c.run()
    assert "reserve" not in c.calls
    with quota.upload_transaction(None, c.fence, c.settings) as session:
        held(tmp_path)
        with pytest.raises(ValueError):
            quota._capacity_guard(session, tmp_path, replace(c.fence, generation=2))
        with pytest.raises(ValueError):
            quota._capacity_guard(session, tmp_path / "other", c.fence)
        with pytest.raises(ValueError):
            with quota.upload_transaction(None, c.fence, c.settings):
                pass
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(ValueError):
                pool.submit(
                    copy_context().run,
                    quota._capacity_guard,
                    session,
                    tmp_path,
                    c.fence,
                ).result()
    with object_storage_guard(tmp_path):
        pass


def test_exact_session_transaction_and_locked_inode_required(monkeypatch, tmp_path):
    c = arrange(monkeypatch, tmp_path)
    with quota.upload_transaction(None, c.fence, c.settings) as session:
        with pytest.raises(ValueError):
            quota._capacity_guard(
                NS(get_transaction=session.get_transaction), tmp_path, c.fence
            )
        old = session.get_transaction
        session.get_transaction = lambda: NS(is_active=True)
        try:
            with pytest.raises(ValueError):
                quota._capacity_guard(session, tmp_path, c.fence)
        finally:
            session.get_transaction = old
        for replace_root in (False, True):
            path = tmp_path if replace_root else tmp_path / "research-objects"
            moved = path.with_name(path.name + "-moved")
            path.rename(moved)
            path.mkdir()
            if replace_root:
                (path / "research-objects").mkdir()
            try:
                with pytest.raises(ValueError, match="directory changed"):
                    quota._capacity_guard(session, tmp_path, c.fence)
            finally:
                if replace_root:
                    (path / "research-objects").rmdir()
                path.rmdir()
                moved.rename(path)
        assert quota._capacity_guard(session, tmp_path, c.fence) is not None
    with object_storage_guard(tmp_path):
        pass


def test_filesystem_and_database_share_original_bounded_window(monkeypatch, tmp_path):
    c = arrange(monkeypatch, tmp_path)
    clock = [10.0]
    monkeypatch.setattr(locks.time, "monotonic", lambda: clock[0])
    original = quota.neutral_transaction

    @contextmanager
    def inspect(*args, **kwargs):
        assert locks.current_window(c.fence).deadline == 15.0
        with original(*args, **kwargs) as session:
            yield session

    monkeypatch.setattr(quota, "neutral_transaction", inspect)
    fd = actual_lock(tmp_path)

    def release(_delay):
        clock[0] = 13.0
        os.close(fd)

    monkeypatch.setattr(locks.time, "sleep", release)
    with locks.acquisition_window(c.fence, 610.0):
        c.run()
        assert locks.current_window(c.fence).deadline == 610.0
    assert quota._admission.get() is None


def test_inactive_transaction_and_closed_descriptor_fail_closed(monkeypatch, tmp_path):
    c = arrange(monkeypatch, tmp_path)
    with quota.upload_transaction(None, c.fence, c.settings) as session:
        transaction = session.get_transaction()
        transaction.is_active = False
        try:
            with pytest.raises(ValueError):
                quota._capacity_guard(session, tmp_path, c.fence)
        finally:
            transaction.is_active = True
        # Corrupt only the private test witness, leaving the real owned guards
        # intact for normal cleanup. A genuinely closed descriptor is never proof.
        value = quota._admission.get()
        closed = os.dup(value[5][1])
        os.close(closed)
        token = quota._admission.set((*value[:5], (value[5][0], closed)))
        try:
            with pytest.raises(OSError):
                quota._capacity_guard(session, tmp_path, c.fence)
        finally:
            quota._admission.reset(token)
    assert quota._admission.get() is None
