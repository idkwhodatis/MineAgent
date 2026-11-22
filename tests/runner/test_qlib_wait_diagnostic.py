"""Deterministic synthetic observer checks; not evidence of a hosted PG result."""

import json
from contextlib import contextmanager
from threading import Event, get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from backend.app.rdagent_operational_handler import OperationalHandler
from runtimes.rdagent_operational import rpc
from tests.integration import rd_qlib_wait_diagnostic as waits
from tests.integration import rd_rpc_diagnostic as diagnostic
from tests.integration.rd_qlib_cost_diagnostic import QlibCost
from tests.rdagent.test_qlib_cost_diagnostic import Channel
from workers.research import rdagent_operational as worker

FIXTURE_URL = URL.create(
    "postgresql+psycopg",
    host="127.0.0.1",
    port=5432,
    database="tradegrad_backup_test_" + "a" * 32,
    username="tradegrad_ci",
    password="b" * 64,
)


class FakeDriver:
    def __init__(self, url, pid):
        self.info = SimpleNamespace(
            host=url.host,
            port=url.port,
            dbname=url.database,
            user=url.username,
            backend_pid=pid,
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"database": "tradegrad_ci"},
        {"host": "private.example"},
        {"port": 9999},
        {"username": "another_user"},
        {"password": "private-password"},
        {"query": {"hostaddr": "127.0.0.1"}},
        {"drivername": "postgresql"},
    ],
)
def test_only_exact_disposable_url_starts_observer(monkeypatch, changed):
    entered = []

    def forbidden(_):
        entered.append(True)
        raise AssertionError

    monkeypatch.setattr(waits, "WaitSampler", forbidden)
    with waits.sample_waits(FIXTURE_URL.set(**changed)) as sampler:
        assert sampler is None
    assert not entered


def test_read_only_connection_has_fresh_snapshots_and_two_time_budgets(monkeypatch):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)

    def wait(_, gen, *, interval, timeout):
        assert gen == "generator"
        return interval, timeout

    monkeypatch.setattr(waits._ReadOnlyConnection, "connect", connect)
    monkeypatch.setattr(waits.Connection, "wait", wait)
    assert waits._connect(FIXTURE_URL) is None
    assert len(calls) == 1
    config = calls[0]
    assert (
        config["options"]
        == "-cdefault_transaction_read_only=on -cstatement_timeout=100"
    )
    assert config["autocommit"] is True and config["prepare_threshold"] is None
    assert config["connect_timeout"] == 2 and config["passfile"] == "/dev/null"
    method = waits._ReadOnlyConnection.wait
    instance = object.__new__(waits._ReadOnlyConnection)
    assert method(instance, "generator") == (0.1, 0.15)
    assert method(instance, "generator", timeout=0.01) == (0.1, 0.01)


def test_readonly_sampling_closed_and_bounded_on_dedicated_thread(monkeypatch, capsys):
    observer_thread = get_ident()
    first_query = Event()
    other_query = Event()
    socket_closed = Event()
    pids = []
    reply = iter(("active", "lock", "io", "wal", "private-wait", None))
    monkeypatch.setattr(waits, "Connection", FakeDriver)
    monkeypatch.setattr(waits, "INTERVAL", 0.005)
    monkeypatch.setattr(waits, "MAX_QUERIES", 6)

    class DB:
        @contextmanager
        def execute(self, query, args):
            assert get_ident() != observer_thread and query is waits.QUERY
            pids.append(args)
            value = next(reply)
            if len(pids) == 1:
                first_query.set()
                assert other_query.wait(1)
            yield SimpleNamespace(fetchone=lambda: (value,) if value else None)

    @contextmanager
    def connect(url):
        assert url is FIXTURE_URL
        try:
            yield DB()
        finally:
            socket_closed.set()

    monkeypatch.setattr(waits, "_connect", connect)
    with waits.sample_waits(FIXTURE_URL) as sampler:
        assert sampler is not None
        foreign_url = FIXTURE_URL.set(database="not-the-bound-db")
        assert (
            sampler.begin(
                SimpleNamespace(driver_connection=FakeDriver(foreign_url, 44)), 22
            )
            is None
        )
        for value in (None, True, 0, 2**31, "private-pid"):
            assert (
                sampler.begin(
                    SimpleNamespace(driver_connection=FakeDriver(FIXTURE_URL, value)),
                    22,
                )
                is None
            )
        raw = SimpleNamespace(driver_connection=FakeDriver(FIXTURE_URL, 314159))
        token = sampler.begin(raw, 22)
        assert first_query.wait(1)
        sampler.end(token)
        # A new invocation with identical PID and ordinal must not receive the old
        # invocation's in-flight response, even if equality would say otherwise.
        next_token = sampler.begin(raw, 22)
        assert token == next_token and token is not next_token
        sampler.end(token)
        other_query.set()
        assert socket_closed.wait(1)
        sampler.end(next_token)
    assert not sampler.thread.is_alive() and sampler.mark is None
    assert pids == [(314159,)] * 6
    report = sampler.report()
    assert report == {
        "status": "limit",
        "rpc_ordinal": None,
        "interval_ms": 5,
        "query_count": 6,
        "boundary_discarded": 1,
        "transactions": [
            {"ordinal": 22, "active": 0, "lock": 1, "io": 1, "wal": 1, "no_sample": 2}
        ],
    }
    assert all(
        private not in json.dumps(report) for private in ("314159", "private", "SELECT")
    )
    assert capsys.readouterr() == ("", "")


def test_no_target_never_connects_and_query_exception_closes_silently(
    monkeypatch, capsys
):
    failed = Event()
    calls = []

    class DB:
        def execute(self, *_):
            raise RuntimeError("private-observer-SQL")

    @contextmanager
    def connect(url):
        calls.append(url)
        try:
            yield DB()
        finally:
            failed.set()

    monkeypatch.setattr(waits, "Connection", FakeDriver)
    monkeypatch.setattr(waits, "_connect", connect)
    monkeypatch.setattr(waits, "INTERVAL", 0.005)
    with waits.sample_waits(FIXTURE_URL) as none:
        pass
    assert not calls and not none.thread.is_alive()
    with waits.sample_waits(FIXTURE_URL) as sampler:
        raw = SimpleNamespace(driver_connection=FakeDriver(FIXTURE_URL, 97243))
        token = sampler.begin(raw, 22)
        assert failed.wait(1)
        sampler.end(token)
    assert not sampler.thread.is_alive() and calls == [FIXTURE_URL]
    assert sampler.report()["status"] == "unavailable"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("stage", ["reserve_object", "finalize_object", None])
def test_marks_only_matched_commit_and_retains_original_driver_error(stage):
    events = []
    token = object()
    private_error = RuntimeError("private-original")

    class Sampler:
        def begin(self, connection, ordinal):
            events.append((connection, ordinal))
            return token

        def end(self, mark):
            events.append(mark)
            raise RuntimeError("private-diagnostic")

    def driver(connection):
        events.append(connection)
        raise private_error

    cost = QlibCost(
        None,
        None,
        method="submit_chat",
        policy="explicit_opt_in",
        child="seeded",
        ordinal=55,
        wait_sampler=Sampler(),
    )
    cost.committing = {
        "transaction_ordinal": 22,
        "ownership": "rpc_fenced_neutral",
        "unit": "provider_publication",
        "resource_scope": "objects",
    }
    cost.publication = "response"
    cost.stages[22] = stage
    with pytest.raises(RuntimeError) as caught:
        cost.wrap_driver(driver)("connection")
    assert caught.value is private_error
    assert events == (
        [("connection", 22), "connection", token] if stage else ["connection"]
    )


@pytest.mark.parametrize("policy", ["default_off", "explicit_opt_in"])
def test_only_fourth_authenticated_scoped_chat_uses_closed_output(policy, capsys):
    engine = create_engine("sqlite://")
    factory = sessionmaker(engine)
    fence = SimpleNamespace(run_id=uuid4(), owner_id=uuid4())
    other = SimpleNamespace(run_id=fence.run_id, owner_id=uuid4())
    sampler = SimpleNamespace(rpc_ordinal=None)
    closed = []

    class Handler(OperationalHandler):
        def __call__(self, peer, method, body):
            assert peer == "private-peer" and method == "submit_chat"
            assert body == {"data": "private-body"}
            return {"ok": True}

    def reject(*_):
        raise rpc.RPCError("private-rejected-auth")

    try:
        with diagnostic.diagnose_rd_rpc(
            qlib_scope=(fence.run_id, fence.owner_id, policy, "first"),
            qlib_wait=sampler,
            qlib_output=closed.append,
        ):
            for authority in (other, fence, fence, other, fence, fence, fence):
                assert (
                    worker.serve_one(
                        Channel("submit_chat"),
                        sequence=0,
                        deadline=diagnostic.monotonic() + 60,
                        authenticate=lambda *_: "private-peer",
                        handle=Handler(factory, authority, None),
                    )
                    == 1
                )
                if authority is other:
                    with pytest.raises(rpc.RPCError, match="private-rejected-auth"):
                        worker.serve_one(
                            Channel("submit_chat"),
                            sequence=0,
                            deadline=diagnostic.monotonic() + 60,
                            authenticate=reject,
                            handle=Handler(factory, fence, None),
                        )
            assert not closed
        [line] = closed
        report = json.loads(line.removeprefix("RD_QLIB_COST_V1:"))
        assert report["rpc_ordinal"] == sampler.rpc_ordinal == 8
        assert report["policy"] == policy
        assert "private" not in line
        std = capsys.readouterr().out
        captured_others = [
            line for line in std.splitlines() if line.startswith("RD_QLIB_COST_V1:")
        ]
        assert len(captured_others) == 4
        assert all('"rpc_ordinal":8' not in line for line in captured_others)
    finally:
        engine.dispose()
