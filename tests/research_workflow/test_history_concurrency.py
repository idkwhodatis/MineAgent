"""HTTP history retries real flock contention; SQL/proof records are doubles."""

import errno
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import factor_native_io as native_io
from backend.app import rdagent_workflow_saved as saved
from backend.app import research_workflow_routes as routes
from backend.app.api.deps import get_current_user
from backend.app.factor_native_io import (
    NativeStorageBusy,
    NativeStorageError,
    native_storage_guard,
    require_native_storage_guard,
)
from backend.app.research_workflow_input_reads import retained_input_guard
from backend.app.services import DomainError
from packages.research_workflow.contracts import (
    WorkflowControls,
    WorkflowStageHistory,
    WorkflowStageView,
    WorkflowView,
)


@pytest.fixture
def history_http(tmp_path, monkeypatch):
    (tmp_path / "factors").mkdir()
    owner, workflow = uuid4(), uuid4()
    history = WorkflowStageHistory(
        stage_id=uuid4(),
        ordinal=1,
        kind="rd_factor_generation",
        input_revision_id=uuid4(),
        input_sha256="a" * 64,
        definition_sha256="b" * 64,
        child_run_id=uuid4(),
        consumption="not_observed",
        checkpoint_id=None,
        source_status="running",
        child_attempt_id=None,
        started_at=None,
        finished_at=None,
        containment="not_observed",
        settlement="not_observed",
    )
    view = WorkflowView(
        workflow_id=workflow,
        revision=1,
        mode="continuous",
        status="running",
        selected_input_revision_id=history.input_revision_id,
        current_stage=WorkflowStageView(
            **{name: getattr(history, name) for name in WorkflowStageView.model_fields}
        ),
        latest_checkpoint_id=None,
        controls=WorkflowControls(reason_code="owner_revision_rechecked_on_command"),
    )
    sessions, verified = [], []
    local = threading.local()

    @contextmanager
    def factory():
        session = NS(closed=False)
        sessions.append(session)
        local.session = session
        try:
            yield session
        finally:
            session.closed = True

    factory.kw = {"bind": object()}

    @contextmanager
    def lifecycle(engine, root):
        local.lifecycle = True
        try:
            yield
        finally:
            local.lifecycle = False

    monkeypatch.setattr(routes, "shared_lifecycle", lifecycle)
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = NS(storage_root=tmp_path)
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: NS(id=owner)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    with TestClient(app) as client:
        yield NS(**locals())


@pytest.mark.parametrize("endpoint", ["detail", "stage"])
@pytest.mark.parametrize("reader", ["submission", "retained_input"])
def test_parallel_history_reads_retry_with_fresh_transactions(
    history_http, monkeypatch, endpoint, reader
):
    h = history_http
    holding, release, retrying = threading.Event(), threading.Event(), threading.Event()

    def verify(session, *args):
        require_native_storage_guard(h.tmp_path)
        assert h.local.lifecycle and not session.closed
        h.verified.append(session)
        if not holding.is_set():
            holding.set()
            assert release.wait(5), "Competing history request never reached backoff"
        return h.history

    # Keep both actual acquisition paths from the browser failure: saved child
    # proof and retained checkpoint/input proof. Only their SQL graph is doubled.
    monkeypatch.setattr(saved, "verify_workflow_submission_under_native_guard", verify)

    def read(session, owner, workflow, *args):
        assert owner == h.owner and workflow == h.workflow
        settings = args[-1]
        if reader == "submission":
            saved.verify_workflow_submission(session, owner, uuid4(), settings)
        else:
            with retained_input_guard(settings):
                verify(session)
        return h.view if endpoint == "detail" else h.history

    monkeypatch.setattr(routes, "read_workflow", read)
    monkeypatch.setattr(routes, "read_stage_history", read)

    def backoff(seconds):
        assert h.local.session.closed and not h.local.lifecycle
        with pytest.raises(NativeStorageError, match="Retained"):
            require_native_storage_guard(h.tmp_path)
        retrying.set()
        time.sleep(seconds)

    monkeypatch.setattr(routes, "time", NS(monotonic=time.monotonic, sleep=backoff))
    url = f"/research-workflows/{h.workflow}"
    if endpoint == "stage":
        url += f"/stages/{h.history.stage_id}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(h.client.get, url)
        try:
            assert holding.wait(5)
            second = pool.submit(h.client.get, url)
            assert retrying.wait(5)
        finally:
            release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    expected = (h.view if endpoint == "detail" else h.history).model_dump(mode="json")
    assert all(
        response.status_code == 200 and response.json() == expected
        for response in results
    )
    assert len(h.sessions) >= 3 and all(session.closed for session in h.sessions)
    assert len(h.verified) == 2 and h.verified[0] is not h.verified[1]
    assert any(
        all(session is not checked for checked in h.verified) for session in h.sessions
    )


def test_history_contention_deadline_returns_retryable_503(history_http, monkeypatch):
    h = history_http
    elapsed, sleeps = [0.0], []

    def backoff(seconds):
        assert h.local.session.closed and not h.local.lifecycle
        elapsed[0] += seconds
        sleeps.append(seconds)

    monkeypatch.setattr(routes, "time", NS(monotonic=lambda: elapsed[0], sleep=backoff))

    def read(session, owner, workflow, settings):
        with retained_input_guard(settings):
            raise AssertionError("A competing publisher still owns the native fence")

    monkeypatch.setattr(routes, "read_workflow", read)
    holding, release = threading.Event(), threading.Event()

    def publisher():
        with native_storage_guard(h.tmp_path):
            holding.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(publisher)
        try:
            assert holding.wait(5)
            response = h.client.get(f"/research-workflows/{h.workflow}")
        finally:
            release.set()
        held.result(timeout=5)
    assert response.status_code == 503
    assert response.json() == {"code": "workflow_history_busy"}
    assert elapsed[0] == pytest.approx(5) and 1 <= len(sleeps) <= 101
    assert len(h.sessions) == len(sleeps)
    assert all(session.closed for session in h.sessions)


def test_history_retry_never_starts_at_or_after_deadline(history_http, monkeypatch):
    h = history_http

    def check(wake_at):
        elapsed, calls = [0.0], []

        def backoff(seconds):
            assert h.local.session.closed and not h.local.lifecycle
            assert seconds == 0.05
            elapsed[0] = wake_at

        monkeypatch.setattr(
            routes, "time", NS(monotonic=lambda: elapsed[0], sleep=backoff)
        )

        def read(*args):
            calls.append(elapsed[0])
            if len(calls) == 1:
                raise NativeStorageBusy("Competing publisher")
            return h.view

        monkeypatch.setattr(routes, "read_workflow", read)
        response = h.client.get(f"/research-workflows/{h.workflow}")
        assert response.status_code == 503
        assert response.json() == {"code": "workflow_history_busy"}
        assert calls == [0.0]

    check(5.0)
    check(6.0)
    assert len(h.sessions) == 2 and all(session.closed for session in h.sessions)


@pytest.mark.parametrize(
    "error",
    [
        NativeStorageError("Factor namespace changed"),
        NativeStorageError("Native byte identity differs"),
        OSError(errno.EIO, "Unrelated storage failure"),
        BlockingIOError(errno.EAGAIN, "Unrelated I/O contention"),
        DomainError(
            "workflow_child_history_changed", "Retained child graph differs", 409
        ),
    ],
)
def test_history_integrity_and_other_io_errors_are_never_retried(
    history_http, monkeypatch, error
):
    h = history_http

    def read(*args):
        raise error

    monkeypatch.setattr(routes, "read_workflow", read)
    if isinstance(error, DomainError):
        response = h.client.get(f"/research-workflows/{h.workflow}")
        assert response.status_code == 409 and response.json() == {"code": error.code}
    else:
        with pytest.raises(type(error)) as caught:
            h.client.get(f"/research-workflows/{h.workflow}")
        assert caught.value is error
    assert len(h.sessions) == 1 and h.sessions[0].closed


@pytest.mark.parametrize("phase", ["acquisition", "exit"])
def test_replaced_native_namespace_withholds_history_without_retry(
    history_http, monkeypatch, phase
):
    h = history_http

    def replace():
        (h.tmp_path / "factors").rename(h.tmp_path / "retained-factors")
        (h.tmp_path / "factors").mkdir()

    if phase == "acquisition":
        flock = native_io.fcntl.flock

        def acquire(fd, operation):
            flock(fd, operation)
            if operation == native_io.fcntl.LOCK_EX | native_io.fcntl.LOCK_NB:
                replace()

        monkeypatch.setattr(native_io.fcntl, "flock", acquire)

    def read(session, owner, workflow, settings):
        with retained_input_guard(settings):
            require_native_storage_guard(h.tmp_path)
            if phase == "exit":
                replace()
        return h.view

    monkeypatch.setattr(routes, "read_workflow", read)
    with pytest.raises(NativeStorageError, match="Factor namespace changed"):
        h.client.get(f"/research-workflows/{h.workflow}")
    assert len(h.sessions) == 1 and h.sessions[0].closed
    assert (h.tmp_path / "retained-factors").is_dir()


@pytest.mark.parametrize("write", [False, True])
def test_original_transaction_never_retries_writes_or_other_reads(history_http, write):
    h = history_http
    error = NativeStorageBusy("Competing publisher")

    def blocked(*args):
        raise error

    # Commands and preflight retain this original one-shot transaction boundary.
    with pytest.raises(NativeStorageBusy) as caught:
        routes._transaction(NS(app=h.app), h.owner, blocked, write=write)
    assert caught.value is error
    assert len(h.sessions) == 1 and h.sessions[0].closed
