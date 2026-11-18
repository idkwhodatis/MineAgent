"""Closed chronology observer fidelity, not real lockholder identification."""

from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.sql_probes import rd_trial_contention_diagnostic as diagnostic


def test_category_remains_active_through_original_commit_and_rollback():
    d = diagnostic.ContentionDiagnostic()
    event.listen(Session, "after_transaction_end", d.transaction_end)
    try:
        for finish in ("commit", "rollback"):
            with Session() as session:
                transaction = session.begin()
                original_result = object()
                assert (
                    d.wrap(lambda s, result=original_result: result, "numeric")(session)
                    is original_result
                )
                d.pulse_failure()
                assert d._events[-1]["active"]["numeric"] == 1
                d.transaction_end(session, SimpleNamespace(parent=object()))
                assert session in d._sessions
                getattr(transaction, finish)()
                d.pulse_failure()
                assert d._events[-1]["active"]["numeric"] == 0
    finally:
        event.remove(Session, "after_transaction_end", d.transaction_end)


def test_original_error_identity_preserved_until_existing_rollback():
    d = diagnostic.ContentionDiagnostic()
    failure = KeyboardInterrupt()

    def original(session):
        raise failure

    with Session() as session:
        transaction = session.begin()
        with pytest.raises(KeyboardInterrupt) as caught:
            d.wrap(original, "controller")(session)
        assert caught.value is failure
        assert d._sessions[session] == "controller"
        transaction.rollback()
        d.transaction_end(session, transaction)
        assert not d._sessions


def test_no_transaction_failure_cannot_leave_phantom_category():
    d = diagnostic.ContentionDiagnostic()
    with Session() as session:
        assert d.wrap(lambda s: 7, "analysis")(session) == 7
        assert not d._sessions


@pytest.mark.parametrize("failure", [OSError("private details"), KeyboardInterrupt()])
def test_observer_and_print_failure_cannot_change_original(monkeypatch, failure):
    d = diagnostic.ContentionDiagnostic()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(d, "_record", fail)
    with Session() as session:
        assert d.wrap(lambda s: "original", "numeric")(session) == "original"
        d.pulse_failure()
        d.body_failure()
    monkeypatch.setattr("builtins.print", fail)
    d.report()


def test_nonblocking_busy_observer_drops_sample_without_waiting():
    d = diagnostic.ContentionDiagnostic()
    assert d._lock.acquire(False)
    try:
        d.pulse_failure()
        assert d._events == []
    finally:
        d._lock.release()


def test_fixed_bounded_output_has_no_session_or_error_data(capsys, monkeypatch):
    monkeypatch.setattr(diagnostic, "monotonic", lambda: 10)
    d = diagnostic.ContentionDiagnostic()
    monkeypatch.setattr(diagnostic, "monotonic", lambda: 10**20)
    for _ in range(diagnostic.MAX_EVENTS + 1):
        d.pulse_failure()
    d.report()
    result = capsys.readouterr().out
    assert len(d._events) == diagnostic.MAX_EVENTS and d._overflow
    assert all(e["elapsed_ms"] == diagnostic.MAX_MS for e in d._events)
    assert "Session" not in result and "0x" not in result
    assert all(
        set(e) == {"kind", "category", "elapsed_ms", "active", "owner_held"}
        for e in d._events
    )


def test_all_original_containment_aliases_restored_on_body_failure():
    from backend.app import (
        analysis_launch_services,
        numeric_launch_services,
        rdagent_controller_recovery,
    )
    from workers.research import analysis_process, numeric_process

    targets = (
        (numeric_launch_services, "record_numeric_containment"),
        (numeric_process, "record_numeric_containment"),
        (analysis_launch_services, "record_analysis_containment"),
        (analysis_process, "record_analysis_containment"),
        (rdagent_controller_recovery, "record_controller_containment"),
    )
    original = [getattr(module, name) for module, name in targets]
    failure = RuntimeError("private original")
    with pytest.raises(RuntimeError) as caught, diagnostic.observe_contention():
        assert all(
            getattr(module, name) is not before
            for (module, name), before in zip(targets, original)
        )
        raise failure
    assert caught.value is failure
    assert [getattr(module, name) for module, name in targets] == original


def test_owner_acquired_only_after_original_success_retained_through_transaction():
    d = diagnostic.ContentionDiagnostic()
    with Session() as session:
        transaction = session.begin()
        d.begin(session, "analysis")
        sentinel = object()

        def original(s, scope):
            assert s is session
            assert not d._held
            return sentinel

        assert d.wrap_owner_lock(original)(session, "submit:private-owner") is sentinel
        assert d._events[-1]["kind"] == "owner_acquired"
        d.pulse_failure()
        assert d._events[-1]["owner_held"]["analysis"] == 1
        transaction.commit()
        d.transaction_end(session, transaction)
        d.pulse_failure()
        assert d._events[-1]["owner_held"]["analysis"] == 0
        assert "private-owner" not in repr(d._events)


def test_failed_owner_lock_does_not_claim_acquisition():
    d = diagnostic.ContentionDiagnostic()
    failure = RuntimeError("private original lock error")

    def original(s, scope):
        raise failure

    with Session() as session:
        d.begin(session, "controller")
        with pytest.raises(RuntimeError) as caught:
            d.wrap_owner_lock(original)(session, "submit:private")
        assert caught.value is failure
        assert not d._held and not any(e["kind"] == "owner_acquired" for e in d._events)


def test_savepoint_acquisition_is_omitted_not_claimed_after_rollback():
    d = diagnostic.ContentionDiagnostic()
    with Session() as session:
        root = session.begin()
        d.begin(session, "numeric")
        nested = session.begin_nested()
        d.wrap_owner_lock(lambda s, scope: None)(session, "submit:private")
        assert not d._held and d._uncertain
        nested.rollback()
        d.pulse_failure()
        assert d._events[-1]["owner_held"]["numeric"] == 0
        root.rollback()


def test_missed_root_end_and_session_reuse_cannot_report_stale_owner():
    d = diagnostic.ContentionDiagnostic()
    with Session() as session:
        root = session.begin()
        d.begin(session, "numeric")
        d.owner_acquired(session)
        assert d._lock.acquire(False)
        try:
            root.rollback()
            d.transaction_end(session, root)
        finally:
            d._lock.release()
        assert d._uncertain
        with session.begin():
            d.pulse_failure()
            assert d._events[-1]["owner_held"]["numeric"] == 0
            d.owner_acquired(session)  # stale tag cannot transfer to new root
            assert d._events[-1]["owner_held"]["numeric"] == 0
            d.begin(session, "analysis")
            d.owner_acquired(session)
            d.pulse_failure()
            assert d._events[-1]["owner_held"] == {
                "numeric": 0,
                "analysis": 1,
                "controller": 0,
            }


def test_local_and_module_imported_owner_aliases_capture_once_and_restore(monkeypatch):
    from backend.app import rdagent_controller_recovery, services

    calls = []

    def original(session, scope):
        calls.append("original")

    monkeypatch.setattr(services, "advisory_lock", original)
    monkeypatch.setattr(rdagent_controller_recovery, "advisory_lock", original)
    with diagnostic.observe_contention() as d:
        for category, function in (
            ("numeric", services.advisory_lock),
            ("controller", rdagent_controller_recovery.advisory_lock),
        ):
            with Session() as session, session.begin():
                d.begin(session, category)
                function(session, "submit:private")
                assert (
                    len(
                        [
                            e
                            for e in d._events
                            if e["kind"] == "owner_acquired"
                            and e["category"] == category
                        ]
                    )
                    == 1
                )
    assert calls == ["original", "original"]
    assert (
        services.advisory_lock is original
        and rdagent_controller_recovery.advisory_lock is original
    )


def test_optional_setup_import_failure_does_not_replace_body_failure(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def fail_target(name, *args, **kwargs):
        if name == "backend.app":
            raise KeyboardInterrupt()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_target)
    failure = RuntimeError("original body")
    with pytest.raises(RuntimeError) as caught, diagnostic.observe_contention() as d:
        assert d._uncertain
        raise failure
    assert caught.value is failure
