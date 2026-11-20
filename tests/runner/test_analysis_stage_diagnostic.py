"""Offline observer behavior, not native deadline or PostgreSQL evidence."""

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from inspect import unwrap

import pytest

from tests.integration import rd_analysis_stage_diagnostic as diagnostic


def report(capsys):
    output = capsys.readouterr().out
    return json.loads(output.removeprefix("RD_ANALYSIS_STAGE_TIMING_V1:"))


def test_original_callback_result_arguments_and_error_identity_are_preserved(capsys):
    observer = diagnostic.Observer()
    token = object()
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return token

    wrapped = observer.wrapped("current_check", original)
    assert observer.root(lambda: wrapped(token, private="secret"))() is token
    assert calls == [((token,), {"private": "secret"})]
    assert unwrap(wrapped) is original
    assert "secret" not in json.dumps(report(capsys))
    error = KeyboardInterrupt("private")

    def failed():
        raise error

    with pytest.raises(KeyboardInterrupt) as caught:
        observer.root(observer.wrapped("read_frame", failed))()
    assert caught.value is error
    assert all(row["ok"] is False for row in report(capsys)["tail"])


def test_intent_projection_then_exact_original_authority_deadline(monkeypatch, capsys):
    from packages.research_runtime.analysis_process_journal import AnalysisIntent
    from runtimes.research_supervisor.analysis_supervisor import AnalysisAuthority

    observer = diagnostic.Observer()
    now = [100.0]
    monkeypatch.setattr(diagnostic, "monotonic", lambda: now[0])
    intent = AnalysisIntent.model_construct(
        not_after=datetime.now(UTC) + timedelta(seconds=8)
    )
    authority = object.__new__(AnalysisAuthority)
    authority.deadline = 108.0

    def original():
        observer.intent(intent)
        observer.record("persist_intent", 100.0, True)
        observer.authority(authority)
        now[0] = 109.0
        observer.record("read_frame", 107.0, False)

    observer.root(original)()
    rows = report(capsys)["tail"]
    assert rows[0]["deadline_source"] == "intent_wall_projection"
    assert 7900 <= rows[0]["unit_remaining_ms"] <= 8000
    assert rows[1]["deadline_source"] == "original_authority_monotonic"
    assert rows[1]["unit_remaining_ms"] == -1000
    assert authority.deadline == 108.0


def test_neutral_interval_includes_original_commit_and_restores_phase(
    monkeypatch, capsys
):
    observer = diagnostic.Observer()
    now = [10.0]
    monkeypatch.setattr(diagnostic, "monotonic", lambda: now[0])
    events = []

    @contextmanager
    def original(*args, **kwargs):
        events.append(("enter", args, kwargs))
        yield "session"
        now[0] += 0.5
        events.append("commit")

    def unit():
        with observer.transaction(original)("same", mode="same") as session:
            assert session == "session"
            observer.phase("intent_transaction")
            now[0] += 0.25
        assert observer.state()["transaction"] is None

    observer.root(unit)()
    rows = report(capsys)["tail"]
    assert rows[0]["stage"] == "intent_transaction" and rows[0]["elapsed_ms"] == 750
    assert events == [("enter", ("same",), {"mode": "same"}), "commit"]


def test_neutral_original_exception_and_suppression_semantics(capsys):
    observer = diagnostic.Observer()
    error = ValueError("private")

    @contextmanager
    def original():
        yield object()

    def unit():
        with observer.transaction(original)():
            raise error

    with pytest.raises(ValueError) as caught:
        observer.root(unit)()
    assert caught.value is error
    assert report(capsys)["tail"][0]["ok"] is False

    @contextmanager
    def suppressing():
        try:
            yield object()
        except ValueError:
            pass

    def suppressed():
        with observer.transaction(suppressing)():
            raise error
        return 7

    assert observer.root(suppressed)() == 7
    report(capsys)


def test_heartbeat_thread_cannot_inherit_analysis_context(capsys):
    observer = diagnostic.Observer()

    def unit():
        thread = threading.Thread(
            target=lambda: observer.record("docker_unknown", 0, True)
        )
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        observer.record("read_frame", 0, True)

    observer.root(unit)()
    rows = report(capsys)["tail"]
    assert [row["stage"] for row in rows] == ["read_frame", "unit"]


def test_failed_record_clock_and_emission_never_replace_worker(monkeypatch):
    observer = diagnostic.Observer()
    error = RuntimeError("original")

    def broken(*_args):
        raise KeyboardInterrupt("observer")

    def original():
        raise error

    monkeypatch.setattr(observer, "record", broken)
    monkeypatch.setattr(observer, "finish", broken)
    monkeypatch.setattr(diagnostic, "monotonic", broken)
    with pytest.raises(RuntimeError) as caught:
        observer.root(original)()
    assert caught.value is error and observer.state() is None
    assert observer.root(lambda: 3)() == 3


def test_closed_labels_bounded_tail_and_unit_count(capsys):
    observer = diagnostic.Observer()

    def unit():
        observer.record("private-label", 0, True)
        for _ in range(200):
            observer.record("docker_unknown", 0, True)

    for _ in range(10):
        observer.root(unit)()
    output = capsys.readouterr().out
    assert output.count("RD_ANALYSIS_STAGE_TIMING_V1:") == 8
    assert "private-label" not in output
    for line in output.splitlines():
        value = json.loads(line.removeprefix("RD_ANALYSIS_STAGE_TIMING_V1:"))
        assert value["samples"] == 201 and len(value["tail"]) == 128
        assert all(
            set(row)
            == {
                "stage",
                "elapsed_ms",
                "unit_remaining_ms",
                "deadline_source",
                "stop_phase",
                "client_remaining_ms",
                "ok",
            }
            for row in value["tail"]
        )


def test_partial_optional_setup_restores_and_preserves_body_error(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch

    target = SimpleNamespace(value=1)

    def broken(stack, observer):
        stack.enter_context(patch.object(target, "value", 2))
        raise KeyboardInterrupt("optional setup")

    monkeypatch.setattr(diagnostic, "_install", broken)
    error = RuntimeError("original")
    with pytest.raises(RuntimeError) as caught, diagnostic.diagnose_analysis_stages():
        assert target.value == 1
        raise error
    assert caught.value is error and target.value == 1


def test_actual_aliases_descriptors_and_unwrapped_identity_restore():
    from runtimes.factor_execution.attach_stream import AttachStream
    from runtimes.research_supervisor.analysis_supervisor import AnalysisSupervisor
    from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient
    from workers.research import analysis_process

    originals = [
        (analysis_process, "execute_analysis_acceptance_unit"),
        (analysis_process, "neutral_transaction"),
        (AnalysisSupervisor, "execute"),
        (NumericDockerClient, "_command"),
    ]
    values = [getattr(target, name) for target, name in originals]
    connect = AttachStream.__dict__["connect"]
    with diagnostic.diagnose_analysis_stages():
        for (target, name), original in zip(originals, values, strict=True):
            assert getattr(target, name) is not original
            assert unwrap(getattr(target, name)) is unwrap(original)
        assert isinstance(AttachStream.__dict__["connect"], classmethod)
        assert unwrap(AttachStream.connect) is connect.__func__
    for (target, name), original in zip(originals, values, strict=True):
        assert getattr(target, name) is original
    assert AttachStream.__dict__["connect"] is connect


def test_commit_events_are_thread_local_and_ignore_savepoints(capsys):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    observer = diagnostic.Observer()
    engine = create_engine("sqlite://")
    before = lambda session: diagnostic._safe(observer.before_commit, session)
    after = lambda session: diagnostic._safe(observer.end_commit, session, True)
    event.listen(Session, "before_commit", before)
    event.listen(Session, "after_commit", after)
    try:

        def unit():
            with Session(engine) as session:
                session.execute(text("SELECT 1"))
                with session.begin_nested():
                    session.execute(text("SELECT 1"))
                observer.state()["containment"] = True
                session.commit()

        observer.root(unit)()
        assert [row["stage"] for row in report(capsys)["tail"]] == [
            "containment_commit",
            "unit",
        ]
    finally:
        event.remove(Session, "before_commit", before)
        event.remove(Session, "after_commit", after)
        engine.dispose()


def test_installed_stop_and_docker_wrappers_distinguish_original_and_fallback(
    monkeypatch, capsys
):
    from types import SimpleNamespace

    from runtimes.research_supervisor import analysis_supervisor as supervisor
    from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient
    from workers.research import analysis_process as worker

    now = [100.0]
    monkeypatch.setattr(diagnostic, "monotonic", lambda: now[0])
    observer = diagnostic.Observer()
    monkeypatch.setattr(diagnostic, "Observer", lambda: observer)
    client = object.__new__(NumericDockerClient)
    client.deadline = None
    owner = SimpleNamespace(client=client)
    error = RuntimeError("private original")
    calls = []

    def command(self, args, *, check=True):
        calls.append((args, check))
        if len(calls) == 2:
            raise error
        return 0, b"private-output"

    def stop(self, root, admission, *, original_deadline=None):
        previous = self.client.deadline
        self.client.deadline = (
            original_deadline if original_deadline is not None else now[0] + 30
        )
        try:
            return self.client._command(["top", "private-id"], check=False)
        finally:
            self.client.deadline = previous

    monkeypatch.setattr(NumericDockerClient, "_command", command)
    monkeypatch.setattr(supervisor.AnalysisSupervisor, "stop_and_verify", stop)

    def unit():
        observer.state()["deadline"] = 108.0
        observer.state()["deadline_source"] = "original_authority_monotonic"
        assert supervisor.AnalysisSupervisor.stop_and_verify(
            owner, "private-root", "private-admission", original_deadline=109.0
        ) == (0, b"private-output")
        now[0] = 109.0
        supervisor.AnalysisSupervisor.stop_and_verify(
            owner, "private-root", "private-admission"
        )

    monkeypatch.setattr(worker, "execute_analysis_acceptance_unit", unit)
    with pytest.raises(RuntimeError) as caught, diagnostic.diagnose_analysis_stages():
        worker.execute_analysis_acceptance_unit()
    assert caught.value is error and client.deadline is None
    rows = report(capsys)["tail"]
    docker = [r for r in rows if r["stage"] == "docker_top"]
    assert [
        (r["stop_phase"], r["unit_remaining_ms"], r["client_remaining_ms"], r["ok"])
        for r in docker
    ] == [
        ("original_deadline_stop", 8000, 9000, True),
        ("fresh_budget_containment", -1000, 30000, False),
    ]
    assert "private" not in json.dumps(rows)
    assert NumericDockerClient._command is command
    assert supervisor.AnalysisSupervisor.stop_and_verify is stop


def test_commit_failure_rollback_and_reused_session_keep_exact_root(capsys):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    observer = diagnostic.Observer()
    engine = create_engine("sqlite://")
    callbacks = (
        ("before_commit", lambda s: diagnostic._safe(observer.before_commit, s)),
        ("after_commit", lambda s: diagnostic._safe(observer.end_commit, s, True)),
        ("after_rollback", lambda s: diagnostic._safe(observer.end_commit, s, False)),
    )
    for name, callback in callbacks:
        event.listen(Session, name, callback)
    error = RuntimeError("original commit listener failure")

    def fail(_session):
        raise error

    try:

        def unit():
            with Session(engine) as session:
                session.execute(text("SELECT 1"))
                event.listen(session, "before_commit", fail)
                with pytest.raises(RuntimeError) as caught:
                    session.commit()
                assert caught.value is error
                session.rollback()
                event.remove(session, "before_commit", fail)
                # A missed end event must not attribute old-root timing to reuse.
                session.execute(text("SELECT 1"))
                old = session.get_transaction()
                observer.before_commit(session)
                event.remove(Session, "after_rollback", callbacks[2][1])
                try:
                    session.rollback()
                finally:
                    event.listen(Session, "after_rollback", callbacks[2][1])
                session.execute(text("SELECT 1"))
                assert session.get_transaction() is not old
                observer.end_commit(session, True)
                session.commit()

        observer.root(unit)()
        rows = [r for r in report(capsys)["tail"] if r["stage"] == "commit"]
        assert [row["ok"] for row in rows] == [False, True]
    finally:
        for name, callback in callbacks:
            event.remove(Session, name, callback)
        engine.dispose()
