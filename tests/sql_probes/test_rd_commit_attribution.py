"""Offline observation tests, not native durability or SQL proof."""

import json
import threading

import pytest

from tests.sql_probes import rd_commit_attribution as m


class Transaction:
    pass


class Session:
    def __init__(self):
        self.root = Transaction()
        self.nested = None

    def get_transaction(self):
        return self.root

    def get_nested_transaction(self):
        return self.nested


def test_concurrent_pulse_cannot_borrow_rpc_label():
    o = m.Observer()
    main, pulse = Session(), Session()
    with o.context("rpc", ("submit_embedding", 71, 200.0)):
        o.before(main)

        def work():
            with o.context("heartbeat"):
                o.before(pulse)
                o.finish(pulse, True)

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        # Completion must use frozen before_commit metadata, not current RPC.
        with o.context("rpc", ("submit_chat", 72, 999.0)):
            o.finish(main, True)
    heartbeat, rpc = o.tail
    assert (
        heartbeat["role"],
        heartbeat["method"],
        heartbeat["sequence"],
        heartbeat["remaining_ms"],
    ) == ("heartbeat", "none", 0, None)
    assert (rpc["role"], rpc["method"], rpc["sequence"]) == (
        "rpc",
        "submit_embedding",
        71,
    )


def test_rollback_and_no_stale_record_on_dropped_finish():
    o = m.Observer(clock=lambda: 5.0)
    s = Session()
    o.before(s)
    with o.lock:
        o.finish(s, True)
    o.finish(s, False)
    assert o.count == 0 and o.dropped
    o.before(s)
    o.finish(s, False)
    assert o.tail[-1]["ok"] is False


def test_bounds_and_secret_free_output(capsys):
    o = m.Observer(clock=lambda: 1e20)
    s = Session()
    s.secret = "never-output-token"
    for _ in range(140):
        o.before(s)
        o.finish(s, True)
    o.report()
    raw = capsys.readouterr().out
    assert "never-output-token" not in raw
    report = json.loads(raw.split(":", 1)[1])
    assert report["count"] == 140 and len(report["tail"]) == 128
    assert report["tail"][0]["site"] == "unclassified"


@pytest.mark.parametrize("error", [OSError("secret"), KeyboardInterrupt("secret")])
def test_clock_and_print_failures_never_replace_body(monkeypatch, error):
    def broken(*a, **k):
        raise error

    o = m.Observer(clock=broken)
    o.before(Session())
    o.finish(Session(), True)
    monkeypatch.setattr("builtins.print", broken)
    o.report()
    original = RuntimeError("original")
    with pytest.raises(RuntimeError) as caught, o.context("heartbeat"):
        raise original
    assert caught.value is original


def test_ast_sites_derived_from_current_original_source():
    sites = m._sites()
    labels = [label for ranges in sites.values() for _, _, label in ranges]
    assert labels == [
        "publication_reserve",
        "publication_preflight",
        "publication_finalize",
        "receipt_bind",
        "provider_lookup",
        "provider_admit",
        "provider_settle",
        "provider_unknown_settle",
    ]
    assert all(low <= high for ranges in sites.values() for low, high, _ in ranges)


def test_unknown_source_shape_disables_mapping(monkeypatch):
    monkeypatch.setattr(
        m.inspect, "getsourcelines", lambda f: (["def changed():\n    pass\n"], 1)
    )
    with pytest.raises(ValueError, match="source layout"):
        m._sites()


def test_wrappers_forward_original_values_errors_and_restore(monkeypatch):
    from runtimes.rdagent_operational import rpc
    from workers.research import rdagent_operational as worker
    from workers.research.lease import AttemptLease

    calls = []
    original = RuntimeError("original")

    def serve(channel, **kw):
        calls.append((channel, kw))
        assert rpc.receive(None) == {"method": "submit_embedding"}
        raise original

    def pulse(*a, **kw):
        calls.append((a, kw))
        return "pulse-result"

    monkeypatch.setattr(worker, "serve_one", serve)
    monkeypatch.setattr(AttemptLease, "pulse", pulse)
    monkeypatch.setattr(rpc, "receive", lambda *a: {"method": "submit_embedding"})
    with m.diagnose_commit_attribution():
        with pytest.raises(RuntimeError) as caught:
            worker.serve_one(
                None, sequence=-7, deadline=31, authenticate=None, handle=None
            )
        assert caught.value is original
        assert AttemptLease.pulse(None, force=True) == "pulse-result"
    assert calls[0][1]["sequence"] == -7
    assert worker.serve_one is serve and AttemptLease.pulse is pulse


def test_partial_setup_failure_does_not_mask_body(monkeypatch):
    original = RuntimeError("body")

    def fail(*a):
        raise KeyboardInterrupt("diagnostic")

    monkeypatch.setattr(m.event, "listen", fail)
    with pytest.raises(RuntimeError) as caught, m.diagnose_commit_attribution():
        raise original
    assert caught.value is original


def test_nested_savepoint_does_not_report_or_consume_root():
    o, session = m.Observer(), Session()
    o.before(session)
    session.nested = Transaction()
    o.before(session)
    o.finish(session, True)
    o.finish(session, False)
    assert o.count == 0
    session.nested = None
    o.finish(session, True)
    assert o.count == 1


def test_missed_root_end_and_session_reuse_drop_stale_record():
    o, session = m.Observer(), Session()
    o.before(session)
    session.root = Transaction()
    o.finish(session, True)
    assert o.count == 0 and o.dropped
    o.before(session)
    o.finish(session, True)
    assert o.count == 1


def test_release_failure_never_escapes():
    class Lock:
        def acquire(self, **kw):
            return True

        def release(self):
            raise KeyboardInterrupt("private")

    o, session = m.Observer(), Session()
    o.lock = Lock()
    o.before(session)
    o.finish(session, True)
    assert o.dropped
    o.report()


def test_same_count_changed_body_disables_site_labels(monkeypatch):
    original = m.inspect.getsourcelines

    def changed(function):
        lines, offset = original(function)
        if function.__name__ == "_publish":
            lines = [
                line.replace(
                    "sha = hashlib.sha256(content).hexdigest()", 'sha = "changed"'
                )
                for line in lines
            ]
        return lines, offset

    monkeypatch.setattr(m.inspect, "getsourcelines", changed)
    with pytest.raises(ValueError, match="source layout"):
        m._sites()


def test_real_sqlalchemy_savepoint_events_not_durability():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session as RealSession

    engine = create_engine("sqlite://")
    o = m.Observer()
    with RealSession(engine) as session:
        event.listen(session, "before_commit", o.before)
        event.listen(session, "after_commit", lambda s: o.finish(s, True))
        event.listen(session, "after_rollback", lambda s: o.finish(s, False))
        with session.begin():
            with session.begin_nested():
                pass
            assert o.count == 0
            nested = session.begin_nested()
            nested.rollback()
            assert o.count == 0
        assert o.count == 1 and o.tail[-1]["ok"]
    engine.dispose()
