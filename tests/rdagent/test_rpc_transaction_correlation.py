"""Observer wiring with real ORM events and RPC codec, not native acceptance."""

import importlib
import json
import sys
from contextlib import contextmanager, nullcontext
from contextvars import copy_context
from threading import Thread
from types import ModuleType

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from runtimes.rdagent_operational import rpc
from tests.integration import rd_rpc_diagnostic as diagnostic


class Channel:
    def __init__(self):
        self.remaining = rpc.encode(
            {
                "schema_version": rpc.SCHEMA,
                "sequence": 71,
                "ticket": "PRIVATE_TICKET",
                "method": "submit_embedding",
                "body": {"request": "PRIVATE_PAYLOAD"},
            }
        )
        self.sent = []

    def settimeout(self, timeout):
        assert timeout > 0

    def recv(self, length):
        out, self.remaining = self.remaining[:length], self.remaining[length:]
        return out

    def sendall(self, data):
        self.sent.append(data)


@pytest.fixture
def observer_fixture(monkeypatch):
    """Inject unopened app boundaries; do not simulate DB/ORM listener behavior."""
    engine = create_engine("sqlite://")
    factory = sessionmaker(bind=engine)
    fence = object()
    now = [10.0]

    def install(name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        package, _, short = name.rpartition(".")
        monkeypatch.setattr(
            importlib.import_module(package), short, module, raising=False
        )
        return module

    @contextmanager
    def neutral(bound_factory, bound_fence, *, resource_scope="none"):
        with bound_factory() as session, session.begin() as transaction:
            session.info["private_neutral_state"] = {
                "fence": bound_fence,
                "transaction": transaction,
                "resource_scope": resource_scope,
            }
            yield session

    lifecycle = install(
        "backend.app.research_lifecycle",
        _neutral_transaction_once=neutral,
        _NEUTRAL_KEY="private_neutral_state",
    )

    class Handler:
        def __init__(self, callback):
            self.factory, self.fence = factory, fence
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

        def _authorize(self):
            with lifecycle._neutral_transaction_once(
                self.factory, self.fence, resource_scope="processes"
            ):
                now[0] += 0.1

    def publication():
        with lifecycle._neutral_transaction_once(
            factory, fence, resource_scope="objects"
        ):
            now[0] += 0.2

    def provider():
        with lifecycle._neutral_transaction_once(
            factory, fence, resource_scope="factor_numeric"
        ):
            now[0] += 0.4
        provider_broker._publish()
        provider_broker._retain_receipt()
        provider_broker.execute()

    worker = install("workers.research.rdagent_operational", serve_one=rpc.serve_one)
    install("backend.app.rdagent_operational_handler", OperationalHandler=Handler)
    install(
        "backend.app.rdagent_operational_broker", submit_fixture_request=publication
    )
    install("backend.app.rdagent_provider_handler", submit_provider_request=provider)
    provider_broker = install(
        "backend.app.rdagent_provider_broker",
        _publish=publication,
        _retain_receipt=publication,
        execute=publication,
    )
    install(
        "tests.integration.rd_analysis_stage_diagnostic",
        diagnose_analysis_stages=nullcontext,
    )
    monkeypatch.setattr(diagnostic, "monotonic", lambda: now[0])
    monkeypatch.setattr(rpc, "time", ModuleType("fixture_time"))
    monkeypatch.setattr(rpc.time, "monotonic", lambda: now[0], raising=False)
    try:
        yield worker, Handler, lifecycle, factory, fence, engine, now
    finally:
        engine.dispose()


def read(capsys):
    out = capsys.readouterr().out
    assert "PRIVATE" not in out
    return json.loads(out.removeprefix("RD_RPC_TIMING:"))


def test_rpc_scope_phase_ordinal_and_original_database_work(observer_fixture, capsys):
    from backend.app import rdagent_provider_handler

    worker, Handler, lifecycle, factory, fence, engine, now = observer_fixture
    expected_sql = []
    original = worker.serve_one
    original_neutral = lifecycle._neutral_transaction_once

    def query(*args):
        expected_sql.append(args[2])

    def auth(*args):
        with lifecycle._neutral_transaction_once(factory, fence):
            return "PRIVATE_AUTHORITY"

    def handle(*args):
        handler._authorize()
        rdagent_provider_handler.submit_provider_request()
        with factory.begin() as session:
            assert session.execute(text("SELECT 1")).scalar_one() == 1
        return {"result": "PRIVATE_RESULT"}

    handler, channel = Handler(handle), Channel()
    event.listen(engine, "before_cursor_execute", query)
    try:
        with diagnostic.diagnose_rd_rpc():
            assert (
                worker.serve_one(
                    channel, sequence=71, deadline=20, authenticate=auth, handle=handler
                )
                == 72
            )
    finally:
        event.remove(engine, "before_cursor_execute", query)
    assert expected_sql == ["SELECT 1"]
    assert worker.serve_one is original
    assert lifecycle._neutral_transaction_once is original_neutral
    assert len(channel.sent) == 1 and now[0] == pytest.approx(11.1)
    report = read(capsys)
    txns = report["transactions"]
    assert [t["unit"] for t in txns] == [
        "outer_auth",
        "handler_authorize",
        "provider_broker",
        "provider_publication",
        "provider_receipt",
        "provider_sdk",
        "handler",
    ]
    assert [t["resource_scope"] for t in txns] == [
        "none",
        "processes",
        "factor_numeric",
        "objects",
        "objects",
        "objects",
        "unconfirmed",
    ]
    assert [t["transaction_ordinal"] for t in txns] == list(range(1, 8))
    assert [t["session_ordinal"] for t in txns] == list(range(1, 8))
    assert {t["rpc_ordinal"] for t in txns} == {1}
    assert {t["sequence"] for t in txns} == {71}
    assert {t["completion"] for t in txns} == {"commit"}
    assert txns[0]["ownership"] == "rpc_fenced_neutral"
    assert txns[-1]["ownership"] == "unconfirmed"


def test_copied_context_other_thread_nested_rollback_and_session_reuse(
    observer_fixture, capsys
):
    worker, Handler, lifecycle, factory, fence, _, _ = observer_fixture
    foreign_done = []

    def foreign():
        with factory.begin():
            foreign_done.append(True)

    def handle(*args):
        foreign_thread = Thread(target=lambda: copied.run(foreign))
        foreign_thread.start()
        foreign_thread.join(timeout=2)
        assert foreign_done == [True] and not foreign_thread.is_alive()
        sentinel = RuntimeError("PRIVATE_SENTINEL")
        with (
            pytest.raises(RuntimeError) as caught,
            lifecycle._neutral_transaction_once(factory, fence),
        ):
            raise sentinel
        assert caught.value is sentinel
        with Session() as shared:
            with shared.begin():
                with shared.begin_nested():
                    pass
                inner = shared.begin_nested()
                inner.rollback()
            with shared.begin():
                pass
        return {}

    def handle_with_context(*args):
        nonlocal copied
        copied = copy_context()
        return handle(*args)

    copied = None
    with diagnostic.diagnose_rd_rpc():
        worker.serve_one(
            Channel(),
            sequence=71,
            deadline=20,
            authenticate=lambda *a: None,
            handle=Handler(handle_with_context),
        )
    txns = read(capsys)["transactions"]
    assert [record["completion"] for record in txns] == ["rollback", "commit", "commit"]
    assert [record["session_ordinal"] for record in txns] == [1, 2, 2]
    assert txns[0]["ownership"] == "rpc_fenced_neutral"
    assert txns[0]["commit_elapsed_ms"] is None


def test_rpc_transaction_bounds_and_same_sequence_new_envelope(
    observer_fixture, capsys
):
    worker, Handler, _, factory, _, _, _ = observer_fixture

    def handle(*args):
        for _ in range(130):
            with factory.begin():
                pass
        return {}

    with diagnostic.diagnose_rd_rpc():
        for _ in range(2):
            worker.serve_one(
                Channel(),
                sequence=71,
                deadline=20,
                authenticate=lambda *a: None,
                handle=Handler(handle),
            )
    report = read(capsys)
    assert len(report["tail"]) == 64
    assert len(report["transactions"]) == 128
    assert report["transaction_counts"] == {
        "tail_evictions": 128,
        "rpc_budget_omissions": 4,
    }
    assert {record["rpc_ordinal"] for record in report["transactions"]} == {2}


def test_outer_commit_interval_crosses_original_deadline_and_cleans_up(
    observer_fixture, capsys
):
    worker, Handler, lifecycle, factory, fence, _, now = observer_fixture
    original_serve = worker.serve_one
    original_neutral = lifecycle._neutral_transaction_once

    def handle(*args):
        # A different exact fence is not attributed even on the same fixture DB.
        with lifecycle._neutral_transaction_once(factory, object()):
            pass
        with lifecycle._neutral_transaction_once(factory, fence) as session:
            now[0] = 19.8

            def cross_deadline(_session):
                now[0] = 20.1

            event.listen(session, "before_commit", cross_deadline)
        return {}

    channel = Channel()
    with pytest.raises(rpc.RPCError, match="^deadline$"), diagnostic.diagnose_rd_rpc():
        worker.serve_one(
            channel,
            sequence=71,
            deadline=20,
            authenticate=lambda *a: None,
            handle=Handler(handle),
        )
    assert not channel.sent
    assert worker.serve_one is original_serve
    assert lifecycle._neutral_transaction_once is original_neutral
    with factory.begin():
        pass
    txns = read(capsys)["transactions"]
    assert len(txns) == 2 and txns[0]["ownership"] == "unconfirmed"
    row = txns[1]
    assert row["ownership"] == "rpc_fenced_neutral" and row["completion"] == "commit"
    assert row["commit_begin_remaining_ms"] == 200
    assert row["commit_elapsed_ms"] == 300
    assert row["transaction_completion_remaining_ms"] == -100
