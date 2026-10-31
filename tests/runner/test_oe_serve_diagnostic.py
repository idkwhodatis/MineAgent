"""Closed observer wiring tests, not a real controller/PG execution claim."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


def load(monkeypatch):
    root = Path(__file__).parents[1] / "integration"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location(
        "serve_diagnostic_test", root / "oe_serve_diagnostic.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_event_dispatch_ack_timing_and_cleanup_fields(monkeypatch):
    d = load(monkeypatch)
    from backend.app import oe_structured_socket_server as server
    from workers.research import oe_structured_controller as issuer

    private = "synthetic_private"
    running = NS(
        deadline=600,
        launch=NS(intent=NS(admission=NS(id=private))),
        measured_exit_code=None,
        measured_oom_killed=None,
        original_deadline_completion=False,
    )
    event = NS(
        epoch_id=private,
        admission_id=private,
        kind="execute_evaluation",
        payload=private,
    )
    monkeypatch.setattr(server, "read_frame", lambda *_: private)
    monkeypatch.setattr(server, "read_event", lambda *_: event)
    monkeypatch.setattr(server, "authenticate_event", lambda *a, **k: private)
    monkeypatch.setattr(server, "apply_event", lambda *a, **k: private)
    monkeypatch.setattr(server, "write_frame", lambda *_: None)
    original_event = server._event
    error = ValueError(private)

    def serve(*a, **k):
        assert server._event(None, None, private, None, None, running, private) is False
        raise error

    monkeypatch.setattr(issuer, "serve_controller", serve)
    monkeypatch.setattr(d, "progress", lambda *_: {"outcomes": 1})
    ticks = iter([10, 101])
    monkeypatch.setattr(d.time, "monotonic", lambda: next(ticks))
    output = []
    with d.diagnose_serve(None, private, lambda *a: output.append(a)):
        with pytest.raises(ValueError) as caught:
            issuer.serve_controller(running=running)
        assert caught.value is error
        # Same cleanup ordering as issuer: after serve fails, before queue returns.
        running.measured_exit_code = 124
        running.measured_oom_killed = False
        running.original_deadline_completion = True
    assert issuer.serve_controller is serve and server._event is original_event
    assert output == [
        (
            "OE_SERVE_PROGRESS_V1:",
            {
                "events": [
                    {
                        "kind": "execute_evaluation",
                        "ack_written": True,
                        "elapsed_ms": 91000,
                        "remaining_ms": 590000,
                    }
                ],
                "count": 1,
                "overflow": False,
                "progress": {"outcomes": 1},
                "unit": {"exit_code": 124, "oom": False, "original_deadline": True},
            },
        )
    ]
    assert private not in json.dumps(output)


def test_unacked_unknown_events_bounded_and_original_error_preserved(monkeypatch):
    d = load(monkeypatch)
    from backend.app import oe_structured_socket_server as server
    from workers.research import oe_structured_controller as issuer

    private = "synthetic_private"
    error = RuntimeError(private)
    monkeypatch.setattr(server, "read_event", lambda *_: NS(kind=private))
    monkeypatch.setattr(server, "write_frame", lambda *_: (_ for _ in ()).throw(error))

    def event(*a, **k):
        server.read_event(private)
        server.write_frame(private)

    monkeypatch.setattr(server, "_event", event)
    monkeypatch.setattr(issuer, "serve_controller", lambda **k: None)
    monkeypatch.setattr(d, "progress", lambda *_: (_ for _ in ()).throw(error))
    output = []
    with d.diagnose_serve(None, private, lambda *a: output.append(a)):
        issuer.serve_controller(
            running=NS(
                deadline=0,
                measured_exit_code=True,
                measured_oom_killed=private,
                original_deadline_completion=1,
            )
        )
        for _ in range(70):
            with pytest.raises(RuntimeError) as caught:
                server._event()
            assert caught.value is error
    result = output[0][1]
    assert result["count"] == 70 and result["overflow"]
    assert len(result["events"]) == 64
    assert all(
        e["kind"] == "other" and e["ack_written"] is False for e in result["events"]
    )
    assert result["unit"] == {
        "exit_code": None,
        "oom": None,
        "original_deadline": False,
    }
    assert result["progress"] == {"unavailable": True}
    assert private not in json.dumps(output)


def test_no_controller_no_extra_output_or_query(monkeypatch):
    d = load(monkeypatch)

    def forbidden(*_):
        raise AssertionError("unexpected observation")

    monkeypatch.setattr(d, "progress", forbidden)
    with d.diagnose_serve(None, None, forbidden):
        pass


def test_scoped_fixed_progress_queries_bound_and_do_not_export_ids(monkeypatch):
    d = load(monkeypatch)
    from sqlalchemy.dialects import postgresql

    queries = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def scalars(self, query):
            queries.append(query)
            return NS(all=lambda: ["synthetic_private"] * 17)

    identity = "00000000-0000-0000-0000-000000000001"
    result = d.progress(Session, identity)
    assert set(result) == {
        "candidates",
        "outcomes",
        "sampling",
        "exchanges",
        "terminal",
        "checkpoints",
    }
    assert set(result.values()) == {17} and len(queries) == 6
    for query in queries:
        compiled = query.compile(dialect=postgresql.dialect())
        assert "run_id =" in str(compiled) and "LIMIT" in str(compiled)
        assert 17 in compiled.params.values()
        assert identity in {str(v) for v in compiled.params.values()}
    assert "synthetic_private" not in json.dumps(result)


def test_source_wiring_closed_inventory_and_no_report_reads(monkeypatch):
    d = load(monkeypatch)
    from backend.app.oe_structured_event_wire import EVENTS, TRANSFERS

    assert d.KINDS == EVENTS | TRANSFERS
    root = Path(__file__).parents[2]
    issuer = ast.parse(
        (root / "workers/research/oe_structured_controller.py").read_text()
    )
    assert any(
        isinstance(n, ast.ImportFrom)
        and n.module == "backend.app.oe_structured_socket_server"
        and any(a.name == "serve_controller" for a in n.names)
        for n in ast.walk(issuer)
    )
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "serve_controller"
        for n in ast.walk(issuer)
    )
    source = Path(d.__file__).read_text()
    assert not any(
        isinstance(n, ast.Attribute)
        and n.attr in {"reports", "reader", "payload", "raw_event", "process_ticket"}
        for n in ast.walk(ast.parse(source))
    )
    queue = (root / "tests/integration/oe_queue_diagnostic.py").read_text()
    assert "diagnose_serve(factory, run_id, emit)" in queue
    assert d.milliseconds(float("nan")) == 600000
    assert d.milliseconds(float("inf")) == 600000
    assert d.milliseconds(-1) == 600000


def test_parse_failure_and_broker_write_do_not_fabricate_event_ack(monkeypatch):
    d = load(monkeypatch)
    from backend.app import oe_structured_socket_server as server
    from workers.research import oe_structured_controller as issuer

    error = ValueError("synthetic_private")

    def read(*_):
        raise error

    def event(*_):
        server.read_event(b"synthetic_private")

    monkeypatch.setattr(server, "read_event", read)
    monkeypatch.setattr(server, "_event", event)
    monkeypatch.setattr(server, "write_frame", lambda *_: None)
    monkeypatch.setattr(issuer, "serve_controller", lambda **k: server._event())
    monkeypatch.setattr(d, "progress", lambda *_: {})
    output = []
    with (
        pytest.raises(ValueError) as caught,
        d.diagnose_serve(None, None, lambda *a: output.append(a)),
    ):
        server.write_frame(b"synthetic_private")  # no active event (broker ACK)
        issuer.serve_controller(running=NS(deadline=0))
    assert caught.value is error
    assert server._event is event and server.read_event is read
    result = output[0][1]
    assert result["events"][0]["kind"] == "other"
    assert result["events"][0]["ack_written"] is False
    assert result["unit"] == {"unavailable": True}
    assert "synthetic_private" not in json.dumps(output)
