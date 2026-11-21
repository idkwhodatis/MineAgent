"""Actual Session/driver boundaries with synthetic SQLite, not a native proof."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from backend.app import rdagent_analysis_finalization as analysis
from backend.app import rdagent_operational_broker as fixture_broker
from backend.app import rdagent_operational_finalization as finalization
from backend.app import rdagent_provider_broker as provider_broker
from backend.app import research_lifecycle as lifecycle
from backend.app import research_object_services as objects
from backend.app.rdagent_operational_handler import OperationalHandler
from packages.rdagent_provider.process_receipts import SDKIntent, SDKStarted, SDKStopped
from runtimes.rdagent_operational import rpc
from tests.integration import rd_qlib_cost_diagnostic as split
from tests.integration import rd_rpc_diagnostic as diagnostic
from workers.research import rdagent_operational as worker


class Base(DeclarativeBase):
    pass


class Row(Base):
    __tablename__ = "synthetic_generation_observation"
    id: Mapped[int] = mapped_column(primary_key=True)


class Channel:
    def __init__(self, method="generation_boundary"):
        self.pending = rpc.encode(
            {
                "schema_version": rpc.SCHEMA,
                "sequence": 0,
                "ticket": "private-ticket",
                "method": method,
                "body": {"data": "private-body"},
            }
        )
        self.sent = []

    def settimeout(self, timeout):
        assert timeout > 0

    def recv(self, size):
        part, self.pending = self.pending[:size], self.pending[size:]
        return part

    def sendall(self, raw):
        self.sent.append(raw)


@pytest.mark.parametrize("matching", [True, False])
def test_only_exact_generation_splits_real_flush_driver_and_verification(
    matching, monkeypatch, capsys
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    ticks, history = [0], []
    fence = SimpleNamespace(run_id=uuid4(), owner_id=uuid4())
    driver = engine.dialect.do_commit
    channel = Channel()

    def now():
        return 1 + ticks[0] / 1e9

    def commit(connection):
        ticks[0] += 31_000_000
        return driver(connection)

    def insert_cost(*args):
        if args[2].startswith("INSERT"):
            ticks[0] += 11_000_000

    @contextmanager
    def neutral(bound, attempt, *, resource_scope):
        with bound.begin() as session:
            session.info[lifecycle._NEUTRAL_KEY] = {
                "transaction": session.get_transaction(),
                "fence": attempt,
                "resource_scope": resource_scope,
            }
            yield session

    def verify(session, *_):
        ticks[0] += 7_000_000
        history.append(session)
        return "private-verified"

    def feedback(_session, *_):
        ticks[0] += 5_000_000
        return "private-feedback"

    def artifact(_session, *_):
        ticks[0] += 3_000_000
        return b"private-artifact"

    def publish(bound, attempt, _settings, _kind, _content, _key):
        for _ in range(3):
            with lifecycle._neutral_transaction_once(
                bound, attempt, resource_scope="objects"
            ) as session:
                session.add(Row())
        return "private-object"

    class Handler(OperationalHandler):
        def __call__(self, peer, method, body):
            for _ in range(4):
                with lifecycle._neutral_transaction_once(
                    self.factory, self.fence, resource_scope="processes"
                ):
                    pass
            with lifecycle._neutral_transaction_once(
                self.factory, self.fence, resource_scope="factor_numeric"
            ) as session:
                assert analysis._snapshot(session) == "private-verified"
                assert finalization._published_feedback(session) == "private-feedback"
                session.add(Row())
                session.flush()
                session.add(Row())
            for kind in ("factor_generation_proposal", "factor_adoption_receipt"):
                assert (
                    finalization._publish(
                        self.factory, self.fence, None, kind, b"private", "private"
                    )
                    == "private-object"
                )
            with lifecycle._neutral_transaction_once(
                self.factory, self.fence, resource_scope="factor_numeric"
            ) as session:
                assert analysis._snapshot(session) == "private-verified"
                assert finalization._published_feedback(session) == "private-feedback"
                assert finalization.read_research_object(session) == b"private-artifact"
                session.add(Row())
                session.flush()
                session.add(Row())
            assert peer == "private-peer" and method == "generation_boundary"
            assert body == {"data": "private-body"}
            return {"ok": True}

    monkeypatch.setattr(engine.dialect, "do_commit", commit)
    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", neutral)
    monkeypatch.setattr(analysis, "_snapshot", verify)
    monkeypatch.setattr(finalization, "_published_feedback", feedback)
    monkeypatch.setattr(finalization, "read_research_object", artifact)
    monkeypatch.setattr(finalization, "_publish", publish)
    monkeypatch.setattr(diagnostic, "monotonic", now)
    monkeypatch.setattr(split, "monotonic_ns", lambda: ticks[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=now))
    event.listen(engine, "before_cursor_execute", insert_cost)
    try:
        target = fence.run_id if matching else uuid4()
        with diagnostic.diagnose_rd_rpc(
            qlib_scope=(target, fence.owner_id, "default_off", "first")
        ):
            assert (
                worker.serve_one(
                    channel,
                    sequence=0,
                    deadline=60,
                    authenticate=lambda *_: "private-peer",
                    handle=Handler(factory, fence, None),
                )
                == 1
            )
            assert "RD_QLIB_COST_V1:" not in capsys.readouterr().out
        assert len(channel.sent) == 1 and len(history) == 2
        assert engine.dialect.do_commit is commit and analysis._snapshot is verify
        assert finalization._publish is publish
        output = capsys.readouterr().out
        assert (
            "private" not in output
            and str(fence.run_id) not in output
            and str(fence.owner_id) not in output
        )
        reports = [
            json.loads(line.removeprefix("RD_QLIB_COST_V1:"))
            for line in output.splitlines()
            if line.startswith("RD_QLIB_COST_V1:")
        ]
        if not matching:
            assert reports == []
            return
        assert len(reports) == 1 and reports[0]["inclusive_overlapping"]
        rows = reports[0]["transactions"]
        assert [r["ordinal"] for r in rows] == [5, 6, 8, 9, 11, 12]
        assert [r["publication"] for r in rows] == [
            None,
            "proposal",
            "proposal",
            "adoption",
            "adoption",
            None,
        ]
        for row in rows:
            components = {c["component"]: c["total_ms"] for c in row["components"]}
            assert row["commit_elapsed_ms"] == 42
            expected = {"orm_flush": 11, "dbapi_commit": 31}
            if row["ordinal"] in {5, 12}:
                expected["verified_analysis"] = 7
                expected["verified_feedback"] = 5
                expected["body_orm_flush"] = 11
            if row["ordinal"] == 12:
                expected["verified_artifact"] = 3
            assert components == expected
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("policy", "matching_owner"),
    [("default_off", True), ("explicit_opt_in", True), ("explicit_opt_in", False)],
)
def test_chat_publication_in_exact_owner_and_policy_splits_actual_commit(
    monkeypatch, capsys, policy, matching_owner
):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    fence = SimpleNamespace(run_id=uuid4(), owner_id=uuid4())
    ticks, calls = [0], []
    driver = engine.dialect.do_commit

    def clock():
        return 1 + ticks[0] / 1e9

    def commit(connection):
        ticks[0] += 31_000_000
        return driver(connection)

    def insert_cost(*args):
        if args[2].startswith("INSERT"):
            ticks[0] += 11_000_000

    @contextmanager
    def neutral(bound, attempt, *, resource_scope):
        with bound.begin() as session:
            session.info[lifecycle._NEUTRAL_KEY] = {
                "transaction": session.get_transaction(),
                "fence": attempt,
                "resource_scope": resource_scope,
            }
            yield session

    def reserve(_session):
        ticks[0] += 2_000_000
        calls.append("reserve")

    def finalize(_session):
        ticks[0] += 3_000_000
        calls.append("finalize")

    def publish(bound, attempt, _settings, _kind, _raw, _key):
        for operation in (fixture_broker.reserve_object, None, objects.finalize_object):
            with lifecycle._neutral_transaction_once(
                bound, attempt, resource_scope="objects"
            ) as session:
                if operation is not None:
                    operation(session)
                session.add(Row())
        return "private-result"

    def retain(bound, attempt, settings, _receipt, _key):
        return provider_broker._publish(
            bound,
            attempt,
            settings,
            "rd_provider_process_receipt",
            b"private",
            "private",
        )

    class Handler(OperationalHandler):
        def __call__(self, peer, method, body):
            assert peer == "private-peer" and method == "submit_chat"
            assert body == {"data": "private-body"}
            a = (self.factory, self.fence, self.settings)
            assert (
                provider_broker._publish(*a, "model_request", b"private", "private")
                == "private-result"
            )
            for receipt in (SDKIntent, SDKStarted, SDKStopped):
                assert (
                    provider_broker._retain_receipt(
                        *a, receipt.model_construct(), "private"
                    )
                    == "private-result"
                )
            assert (
                provider_broker._publish(*a, "model_response", b"private", "private")
                == "private-result"
            )
            provider_broker._publish(*a, "private-unlisted-kind", b"private", "private")
            with lifecycle._neutral_transaction_once(
                self.factory, self.fence, resource_scope="objects"
            ) as session:
                session.add(Row())
            return {"ok": True}

    monkeypatch.setattr(engine.dialect, "do_commit", commit)
    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", neutral)
    monkeypatch.setattr(fixture_broker, "reserve_object", reserve)
    monkeypatch.setattr(objects, "finalize_object", finalize)
    monkeypatch.setattr(provider_broker, "_publish", publish)
    monkeypatch.setattr(provider_broker, "_retain_receipt", retain)
    monkeypatch.setattr(diagnostic, "monotonic", clock)
    monkeypatch.setattr(split, "monotonic_ns", lambda: ticks[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=clock))
    event.listen(engine, "before_cursor_execute", insert_cost)
    channel = Channel("submit_chat")
    try:
        owner = fence.owner_id if matching_owner else uuid4()
        with diagnostic.diagnose_rd_rpc(
            qlib_scope=(fence.run_id, owner, policy, "first")
        ):
            assert (
                worker.serve_one(
                    channel,
                    sequence=0,
                    deadline=60,
                    authenticate=lambda *_: "private-peer",
                    handle=Handler(factory, fence, None),
                )
                == 1
            )
            assert "RD_QLIB_COST_V1:" not in capsys.readouterr().out
        assert calls == ["reserve", "finalize"] * 6 and len(channel.sent) == 1
        assert (
            engine.dialect.do_commit is commit and provider_broker._publish is publish
        )
        out = capsys.readouterr().out
        assert (
            "private" not in out
            and str(owner) not in out
            and str(fence.run_id) not in out
        )
        lines = [
            line.split(":", 1)[1]
            for line in out.splitlines()
            if line.startswith("RD_QLIB_COST_V1:")
        ]
        if not matching_owner:
            assert not lines
            return
        assert len(lines) == 1
        report = json.loads(lines[0])
        assert (report["method"], report["policy"], report["child"]) == (
            "submit_chat",
            policy,
            "first",
        )
        rows = report["transactions"]
        assert [r["ordinal"] for r in rows] == list(range(1, 16))
        assert [r["publication"] for r in rows] == [
            kind
            for kind in (
                "request",
                "receipt_intent",
                "receipt_started",
                "receipt_stopped",
                "response",
            )
            for _ in range(3)
        ]
        assert [r["publication_stage"] for r in rows] == [
            "reserve_object",
            None,
            "finalize_object",
        ] * 5
        for row in rows:
            samples = {
                item["component"]: item["total_ms"] for item in row["components"]
            }
            expected = {"orm_flush": 11, "dbapi_commit": 31}
            if row["publication_stage"] == "reserve_object":
                expected["reserve_object"] = 2
            elif row["publication_stage"] == "finalize_object":
                expected["finalize_object"] = 3
            assert samples == expected and row["commit_elapsed_ms"] == 42
    finally:
        engine.dispose()


def test_observer_fault_and_driver_fault_preserve_call_and_closed_output(monkeypatch):
    row = {
        "transaction_ordinal": 5,
        "resource_scope": "factor_numeric",
        "ownership": "rpc_fenced_neutral",
        "transaction_ms": 1,
        "commit_elapsed_ms": 1,
        "commit_begin_remaining_ms": 1,
        "completion": "rollback",
    }
    cost = split.QlibCost(
        None,
        lambda _session: row,
        method="generation_boundary",
        policy="default_off",
        child="first",
        ordinal=1,
    )
    cost.committing = row
    original = RuntimeError("private-original-driver")
    calls = []

    def driver(connection):
        calls.append(connection)
        raise original

    with pytest.raises(RuntimeError) as caught:
        cost.wrap_driver(driver)(None)
    assert caught.value is original and calls == [None]
    cost.end(row)
    payload = cost.report()["transactions"][0]["components"]
    assert len(payload) == 1 and payload[0]["failures"] == 1
    assert "private" not in json.dumps(payload)

    def broken():
        raise RuntimeError("private-observer")

    monkeypatch.setattr(split, "monotonic_ns", broken)
    cost.committing = row
    with pytest.raises(RuntimeError) as caught:
        cost.wrap_driver(driver)(None)
    assert caught.value is original and calls == [None, None]
