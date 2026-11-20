"""Observer mechanics, not native PostgreSQL/lifecycle acceptance."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.research_lifecycle import _NEUTRAL_KEY
from workers.research import rdagent_operational as operational
from workers.research.lease import AttemptLease


def module(monkeypatch):
    base = Path(__file__).parents[1] / "integration"
    for name in (
        "rd_lease_diagnostic",
        "rd_owner_lock_diagnostic",
        "rd_timeline_diagnostic",
    ):
        spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, loaded)
        spec.loader.exec_module(loaded)
    return loaded


def read(capsys):
    text = capsys.readouterr().out
    assert "PRIVATE" not in text
    return json.loads(text.removeprefix("RD_TIMELINE_V1:"))


def test_commit_noop_recovery_and_terminal_order(monkeypatch, capsys):
    observer = module(monkeypatch)
    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine)
    lease = SimpleNamespace(factory=factory)

    def pulse(lease, force=False):
        if force:
            with lease.factory.begin():
                pass
        return "unchanged"

    def recovery(bound_factory, *args, **kwargs):
        with bound_factory.begin():
            pass
        return "recovered"

    monkeypatch.setattr(AttemptLease, "pulse", pulse)
    monkeypatch.setattr(operational, "_recover_and_record", recovery)
    with observer.diagnose_rd_timeline(factory):
        assert AttemptLease.pulse(lease) == "unchanged"
        assert AttemptLease.pulse(lease, force=True) == "unchanged"
        assert operational._recover_and_record(factory) == "recovered"
        with factory.begin() as session:
            session.info[_NEUTRAL_KEY] = {"terminal": ("PRIVATE",)}
    events = read(capsys)["events"]
    assert [e["event"] for e in events] == [
        "pulse_enter",
        "pulse_return",
        "pulse_enter",
        "pulse_db_commit",
        "pulse_return",
        "recovery_enter",
        "recovery_db_commit",
        "recovery_return",
        "terminal_db_commit",
    ]
    assert [e["at_ms"] for e in events] == sorted(e["at_ms"] for e in events)
    assert AttemptLease.pulse is pulse
    assert operational._recover_and_record is recovery
    engine.dispose()


def test_exact_exception_and_recovery_precede_pulse_error(monkeypatch, capsys):
    observer = module(monkeypatch)
    factory = sessionmaker(bind=create_engine("sqlite://"))
    failure = RuntimeError("PRIVATE original")

    def fail(*args, **kwargs):
        raise failure

    def recover(*args, **kwargs):
        with pytest.raises(RuntimeError) as raised:
            AttemptLease.pulse(SimpleNamespace(factory=factory))
        assert raised.value is failure

    monkeypatch.setattr(AttemptLease, "pulse", fail)
    monkeypatch.setattr(operational, "serve_one", fail)
    monkeypatch.setattr(operational, "_recover_and_record", recover)
    with pytest.raises(RuntimeError) as raised, observer.diagnose_rd_timeline(factory):
        try:
            operational.serve_one()
        except RuntimeError:
            operational._recover_and_record(factory)
            raise
    assert raised.value is failure
    assert [e["event"] for e in read(capsys)["events"]] == [
        "serve_one_error",
        "recovery_enter",
        "pulse_enter",
        "pulse_error",
        "recovery_return",
    ]
    assert operational.serve_one is fail


def test_bounds_unrelated_factory_and_broken_output(monkeypatch, capsys):
    observer = module(monkeypatch)
    factory = sessionmaker(bind=create_engine("sqlite://"))
    other = sessionmaker(bind=create_engine("sqlite://"))
    monkeypatch.setattr(AttemptLease, "pulse", lambda *a, **k: 42)
    with observer.diagnose_rd_timeline(factory):
        assert AttemptLease.pulse(SimpleNamespace(factory=other)) == 42
        for _ in range(600):
            assert AttemptLease.pulse(SimpleNamespace(factory=factory)) == 42
    report = read(capsys)
    assert report["at_capacity"] and len(report["events"]) == 1024
    failure = RuntimeError("PRIVATE original")

    def broken(*args, **kwargs):
        raise OSError("PRIVATE output")

    monkeypatch.setattr(observer, "print", broken, raising=False)
    with pytest.raises(RuntimeError) as raised, observer.diagnose_rd_timeline(factory):
        raise failure
    assert raised.value is failure


def test_claimed_queue_factory_and_savepoint_filter(monkeypatch, capsys):
    from uuid import UUID

    from workers.research import rdagent_queue

    observer = module(monkeypatch)
    fixture = sessionmaker(bind=create_engine("sqlite://"))
    queued = sessionmaker(bind=create_engine("sqlite://"))
    run_id = UUID(int=1)
    sentinel = object()
    monkeypatch.setattr(rdagent_queue, "claim_configured_run", lambda *a, **k: sentinel)

    def pulse(lease):
        with lease.factory.begin() as session, session.begin_nested():
            pass

    monkeypatch.setattr(AttemptLease, "pulse", pulse)
    with observer.diagnose_rd_timeline(fixture, run_id):
        assert rdagent_queue.claim_configured_run(queued, run_id) is sentinel
        AttemptLease.pulse(SimpleNamespace(factory=queued))
    assert [e["event"] for e in read(capsys)["events"]] == [
        "pulse_enter",
        "pulse_db_commit",
        "pulse_return",
    ]


@pytest.mark.parametrize("outcome", ["commit", "rollback", "close"])
def test_owner_acquisition_paired_through_outer_transaction(
    monkeypatch, capsys, outcome
):
    from backend.app import services
    from workers.research import numeric_process

    observer = module(monkeypatch)
    factory = sessionmaker(bind=create_engine("sqlite://"))
    calls = []

    def owner_lock(session, scope):
        calls.append(scope)
        return "unchanged"

    def contain(session, *args, **kwargs):
        assert services.advisory_lock(session, "submit:PRIVATE") == "unchanged"
        return 73

    monkeypatch.setattr(services, "advisory_lock", owner_lock)
    monkeypatch.setattr(numeric_process, "record_numeric_containment", contain)
    with observer.diagnose_rd_timeline(factory):
        session = factory()
        session.begin()
        assert numeric_process.record_numeric_containment(session) == 73
        # Containment helper already returned: tracking must survive until commit.
        with session.begin_nested():
            pass
        if outcome == "close":
            session.close()
        else:
            getattr(session, outcome)()
            session.close()
    events = read(capsys)["events"]
    assert calls == ["submit:PRIVATE"]
    assert [event["event"] for event in events] == [
        "owner_lock_acquired",
        "owner_transaction_" + ("end_unclassified" if outcome == "close" else outcome),
    ]
    assert {event["owner_role"] for event in events} == {"numeric_containment"}
    assert {event["transaction_ordinal"] for event in events} == {1}
    assert services.advisory_lock is owner_lock
    assert numeric_process.record_numeric_containment is contain


def test_neutral_scope_and_failed_acquisition_are_distinct(monkeypatch, capsys):
    from contextlib import contextmanager

    from backend.app import research_lifecycle as lifecycle

    observer = module(monkeypatch)
    factory = sessionmaker(bind=create_engine("sqlite://"))
    error = RuntimeError("PRIVATE owner busy")

    def owner_lock(session, scope):
        if scope == "submit:PRIVATE_FAILURE":
            raise error

    @contextmanager
    def neutral(factory, fence, *, resource_scope):
        with factory.begin() as session:
            lifecycle._try_neutral_lock(session, fence)
            yield session

    monkeypatch.setattr(lifecycle, "_try_neutral_lock", owner_lock)
    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", neutral)
    with observer.diagnose_rd_timeline(factory):
        with lifecycle._neutral_transaction_once(
            factory, "submit:PRIVATE", resource_scope="processes"
        ):
            pass
        with (
            pytest.raises(RuntimeError) as raised,
            lifecycle._neutral_transaction_once(
                factory, "submit:PRIVATE_FAILURE", resource_scope="factor_numeric"
            ),
        ):
            pass
        assert raised.value is error
    events = read(capsys)["events"]
    assert [event["event"] for event in events] == [
        "owner_lock_acquired",
        "owner_transaction_commit",
    ]
    assert {event["owner_role"] for event in events} == {"neutral_processes"}


@pytest.mark.parametrize("label", ["analysis", "controller"])
def test_containment_roles_and_observer_failure_preserve_result(
    monkeypatch, capsys, label
):
    from backend.app import rdagent_controller_recovery as controller
    from backend.app import services
    from workers.research import analysis_process

    observer = module(monkeypatch)
    factory = sessionmaker(bind=create_engine("sqlite://"))
    target = analysis_process if label == "analysis" else controller
    lock_module = services if label == "analysis" else controller
    monkeypatch.setattr(lock_module, "advisory_lock", lambda *a: None)

    def contain(session):
        lock_module.advisory_lock(session, "submit:PRIVATE")
        return 17

    name = "record_" + label + "_containment"
    monkeypatch.setattr(target, name, contain)
    with observer.diagnose_rd_timeline(factory), factory.begin() as session:
        assert getattr(target, name)(session) == 17
    assert {event["owner_role"] for event in read(capsys)["events"]} == {
        label + "_containment"
    }

    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE observer")

    owner_observer = sys.modules["rd_owner_lock_diagnostic"]
    with (
        owner_observer.observe_owner_transactions(broken, lambda session: True),
        factory.begin() as session,
    ):
        assert getattr(target, name)(session) == 17


def test_savepoint_rollback_does_not_end_tracked_outer_lock(monkeypatch, capsys):
    from backend.app import services
    from workers.research import numeric_process

    observer = module(monkeypatch)
    factory = sessionmaker(bind=create_engine("sqlite://"))
    monkeypatch.setattr(services, "advisory_lock", lambda *a: None)

    def contain(session):
        services.advisory_lock(session, "submit:PRIVATE")

    monkeypatch.setattr(numeric_process, "record_numeric_containment", contain)
    with observer.diagnose_rd_timeline(factory), factory.begin() as session:
        numeric_process.record_numeric_containment(session)
        nested = session.begin_nested()
        nested.rollback()
    assert [event["event"] for event in read(capsys)["events"]] == [
        "owner_lock_acquired",
        "owner_transaction_commit",
    ]
