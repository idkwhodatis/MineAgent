"""Actual RPC codec with explicit channel/clock doubles; no Docker timing claim."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from runtimes.rdagent_operational import rpc
from workers.research import rdagent_operational as worker


def _diagnostic():
    path = Path(__file__).parents[1] / "integration/rd_rpc_diagnostic.py"
    spec = importlib.util.spec_from_file_location("rd_rpc_diagnostic_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Channel:
    def __init__(self):
        self.raw = rpc.encode(
            {
                "schema_version": rpc.SCHEMA,
                "sequence": 0,
                "ticket": "secret-ticket",
                "method": "execute_or_reuse",
                "body": {"secret": "private-payload"},
            }
        )
        self.sent = []

    def settimeout(self, value):
        assert value > 0

    def recv(self, count):
        chunk, self.raw = self.raw[:count], self.raw[count:]
        return chunk

    def sendall(self, raw):
        self.sent.append(raw)


def test_actual_rpc_timing_preserves_deadline_and_redacts_payload(monkeypatch, capsys):
    module = _diagnostic()
    now = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: now[0]))
    channel = Channel()
    observed = []
    original = worker.serve_one

    def handler(authority, method, body):
        observed.append((authority, method, body))
        now[0] = 60.001
        return {"private-result": "secret"}

    with (
        pytest.raises(rpc.RPCError, match="^deadline$") as caught,
        module.diagnose_rd_rpc(),
    ):
        worker.serve_one(
            channel,
            sequence=0,
            deadline=60,
            authenticate=lambda *_: "actual-authority",
            handle=handler,
        )
    assert len(observed) == 1 and not channel.sent
    assert module._queue_failure(caught.value)["rpc_method"] == "execute_or_reuse"
    assert "observer_failure" not in module._queue_failure(caught.value)
    assert worker.serve_one is original
    output = capsys.readouterr().out
    assert all(v not in output for v in ("secret", "private", "actual-authority"))
    data = json.loads(output.removeprefix("RD_RPC_TIMING:"))
    assert [item["stage"] for item in data["tail"]] == [
        "read",
        "auth",
        "handle",
        "send",
    ]
    assert data["tail"][-1]["remaining_ms"] == -1
    assert data["tail"][-1]["ok"] is False


def test_actual_rpc_timing_observes_real_session_commit_without_changing_reply(
    monkeypatch, capsys
):
    module = _diagnostic()
    now = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: now[0]))
    channel = Channel()
    engine = create_engine("sqlite://")

    def handler(*_):
        with Session(engine) as session, session.begin():
            now[0] += 0.2
        return {"ok": True}

    try:
        with module.diagnose_rd_rpc():
            assert (
                worker.serve_one(
                    channel,
                    sequence=0,
                    deadline=60,
                    authenticate=lambda *_: None,
                    handle=handler,
                )
                == 1
            )
    finally:
        engine.dispose()
    assert len(channel.sent) == 1
    data = json.loads(capsys.readouterr().out.removeprefix("RD_RPC_TIMING:"))
    assert [item["stage"] for item in data["tail"]] == [
        "read",
        "auth",
        "commit",
        "handle",
        "send",
    ]
    assert all(item["ok"] for item in data["tail"])


def test_queue_diagnostic_preserves_exception_and_redacts_unknown_codes(
    monkeypatch, capsys
):
    from packages.research_runtime.process_contracts import ProcessError
    from workers.research import rdagent_queue

    module = _diagnostic()
    error = ProcessError("secret-source-url-credential")
    seen = []

    def execute(*args, **kwargs):
        seen.append((args, kwargs))
        raise error

    monkeypatch.setattr(rdagent_queue, "execute", execute)
    monkeypatch.setattr(module, "_queue_state", lambda *a: {"status": "failed"})
    with (
        pytest.raises(ProcessError) as raised,
        module.diagnose_rd_queue(object(), "private-run-id"),
    ):
        rdagent_queue.execute("private-run-id", 1, settings="private-settings")
    assert raised.value is error and rdagent_queue.execute is execute
    assert len(seen) == 1
    output = capsys.readouterr().out
    assert not any(s in output for s in ("secret", "private", "credential", "url"))
    lines = output.splitlines()
    assert json.loads(lines[0].removeprefix("RD_QUEUE_FAILURE:")) == {
        "family": "process",
        "code": "unknown",
        "stage": "unknown",
        "sqlstate": "none",
    }
    assert lines[1].startswith("RD_QUEUE_STATE:")
    assert module._queue_failure(rpc.RPCError("deadline"))["code"] == "deadline"
    assert module._queue_failure(rpc.RPCError("secret"))["code"] == "unknown"

    def unavailable(*args):
        raise ValueError("secret-database-url")

    monkeypatch.setattr(module, "_queue_state", unavailable)
    with (
        pytest.raises(ProcessError) as repeated,
        module.diagnose_rd_queue(object(), "private-run-id"),
    ):
        rdagent_queue.execute("private-run-id", 1, settings="private-settings")
    assert repeated.value is error and rdagent_queue.execute is execute
    output = capsys.readouterr().out
    assert "diagnostic_unavailable" in output and "secret" not in output

    # Real serve_one frames may contain hostile request values, never emit them.
    class HostileDict(dict):
        def get(self, *args):
            raise AssertionError("private mapping must not be inspected")

    class HostileString(str):
        def __hash__(self):
            raise AssertionError("private string must not be hashed")

    for request in (
        {"method": "private-secret-method"},
        {"method": {"private": "secret"}},
        {"method": HostileString("execute_or_reuse")},
        HostileDict(method="execute_or_reuse"),
    ):
        with monkeypatch.context() as patcher:
            patcher.setattr(rpc, "receive", lambda *_, request=request: request)
            with pytest.raises(rpc.RPCError) as caught:
                rpc.serve_one(
                    None, sequence=0, deadline=1, authenticate=None, handle=None
                )
            report = module._queue_failure(caught.value)
        assert report["rpc_method"] == "unknown"
        assert "private" not in json.dumps(report)

    def reject(*_):
        raise RuntimeError("private handler failure")

    for method in sorted(rpc.METHODS):
        request = {
            "schema_version": rpc.SCHEMA,
            "sequence": 0,
            "ticket": "private",
            "method": method,
            "body": {"private": "secret"},
        }
        with monkeypatch.context() as patcher:
            patcher.setattr(rpc, "receive", lambda *_, request=request: request)
            with pytest.raises(RuntimeError) as caught:
                rpc.serve_one(
                    None,
                    sequence=0,
                    deadline=1,
                    authenticate=lambda *_: None,
                    handle=reject,
                )
            report = module._queue_failure(caught.value)
        assert report["rpc_method"] == method
        assert "private" not in json.dumps(report)

    from runtimes.rdagent_operational import daemon_identity as observer

    def observed_failure(call, expected):
        with pytest.raises(ProcessError) as caught:
            call()
        report = module._queue_failure(caught.value)
        assert report["observer_failure"] == expected
        assert "private" not in json.dumps(report)

    observed_failure(lambda: observer.parse_top(b"private-secret"), "daemon_top")
    observed_failure(
        lambda: observer._inspect(
            SimpleNamespace(_inspect=lambda _: {}), "private", "private", 1
        ),
        "container_state",
    )
    with monkeypatch.context() as patcher:
        patcher.setattr(observer, "_read", lambda *_: "2 (private)")
        observed_failure(lambda: observer._host_snapshot(1), "host_snapshot")
    with monkeypatch.context() as patcher:
        from unittest.mock import MagicMock

        path = MagicMock()
        path.__truediv__.return_value = path
        path.open.return_value.__enter__.return_value.read.return_value = b""
        patcher.setattr(observer, "Path", lambda _: path)
        observed_failure(lambda: observer._read(1, "private", 1), "host_read")
    parameters = {
        "container_id": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "leader_pid": 1,
        "pid": 1,
        "deadline": 1,
    }
    observed_failure(lambda: observer._observe(**{**parameters, "pid": -1}), "unknown")
    with monkeypatch.context() as patcher:
        patcher.setattr(observer, "DockerSupervisor", lambda _: object())
        patcher.setattr(observer, "time", SimpleNamespace(monotonic=lambda: 2))
        observed_failure(lambda: observer._observe(**parameters), "deadline")

    # A matching module/function name alone is not a trusted code identity.
    from types import FunctionType

    def spoof():
        raise ProcessError("rd_controller_daemon_identity")

    fake = FunctionType(
        spoof.__code__.replace(co_name="_observe"),
        {"__name__": observer.__name__, "ProcessError": ProcessError},
        closure=spoof.__closure__,
    )
    with pytest.raises(ProcessError) as caught:
        fake()
    assert "observer_failure" not in module._queue_failure(caught.value)


def test_queue_state_is_bounded_and_only_emits_allowlisted_fields():
    from unittest.mock import MagicMock
    from uuid import uuid4

    module = _diagnostic()
    session = MagicMock()
    session.get.return_value = SimpleNamespace(status="secret-status")
    session.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="failed",
            error="secret-message",
            sandbox_stopped=True,
            exit_code=True,
            worker_id="secret-worker",
            sandbox_id="secret-container",
        )
    ] * 9
    session.execute.return_value.all.return_value = [
        ("secret-role", "secret-state")
    ] * 17
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    state = module._queue_state(factory, uuid4())
    assert state["attempt_overflow"] and len(state["attempts"]) == 8
    assert state["unit_overflow"] and len(state["units"]) == 16
    assert state["status"] == "unknown"
    assert state["attempts"][0]["exit_code"] is None
    assert state["attempts"][0]["error"] == "other"
    assert "secret" not in json.dumps(state)


@pytest.mark.parametrize(
    "error_type, expected",
    [(AttributeError, "attribute"), (TypeError, "type"), (KeyError, "key")],
)
def test_closed_failure_builtin_families_do_not_render_payload(error_type, expected):
    report = _diagnostic()._queue_failure(error_type("private-secret-source"))
    assert report == {
        "family": expected,
        "code": "unknown",
        "stage": "unknown",
        "sqlstate": "none",
    }
    assert "private" not in json.dumps(report)


def test_validation_and_non_dbapi_orm_families_are_closed():
    from pydantic import BaseModel, ValidationError
    from sqlalchemy.orm.exc import DetachedInstanceError

    class ClosedValue(BaseModel):
        value: int

    with pytest.raises(ValidationError) as caught:
        ClosedValue(value="private-secret-value")
    diagnostic = _diagnostic()
    assert diagnostic._queue_failure(caught.value)["family"] == "validation"
    report = diagnostic._queue_failure(DetachedInstanceError("private-owner-path"))
    assert report == {
        "family": "orm",
        "code": "unknown",
        "stage": "unknown",
        "sqlstate": "none",
    }
    assert "private" not in json.dumps(diagnostic._queue_failure(caught.value))


@pytest.mark.parametrize(
    "module_name, function, stage",
    [
        (
            "backend.app.rdagent_numeric_failure_finalization",
            "_closed_phase",
            "failure_phase",
        ),
        (
            "backend.app.rdagent_numeric_failure_finalization",
            "retain_numeric_failure_feedback",
            "failure_finalize",
        ),
        (
            "backend.app.rdagent_services",
            "_completed_member_response",
            "member_response",
        ),
        ("backend.app.rdagent_operational_broker", "_publish", "object_publish"),
        ("backend.app.research_object_services", "reserve_object", "object_reserve"),
        (
            "backend.app.research_object_services",
            "publish_reserved_object",
            "object_persist",
        ),
        (
            "packages.rdagent_adapter.numeric_failure",
            "upstream_observation",
            "failure_document",
        ),
    ],
)
def test_only_fixed_failure_function_stages_are_rendered(module_name, function, stage):
    from types import FunctionType

    def fail():
        raise TypeError("private-payload")

    target = FunctionType(
        fail.__code__.replace(co_name=function, co_filename="/private/path"),
        {"__name__": module_name},
        function,
    )
    with pytest.raises(TypeError) as caught:
        target()
    report = _diagnostic()._queue_failure(caught.value)
    assert report == {
        "family": "type",
        "code": "unknown",
        "stage": stage,
        "sqlstate": "none",
    }
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    "error,expected",
    [
        (TimeoutError("private-payload"), "deadline"),
        (OSError("private-payload"), "daemon_top"),
    ],
)
@pytest.mark.parametrize("wrapped", [False, True])
def test_shared_top_transport_failure_has_closed_diagnostic(
    monkeypatch, error, expected, wrapped
):
    from unittest.mock import MagicMock, Mock

    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.rdagent_operational import daemon_identity as observer
    from runtimes.research_supervisor import daemon_top

    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.connect.side_effect = error
    factory = Mock(return_value=connection)
    monkeypatch.setattr(daemon_top.socket, "socket", factory)
    monkeypatch.setattr(daemon_top, "time", SimpleNamespace(monotonic=lambda: 10))
    monkeypatch.setattr(observer, "time", SimpleNamespace(monotonic=lambda: 10))
    cli = SimpleNamespace(_command=Mock(side_effect=AssertionError("no fallback")))
    monkeypatch.setattr(observer, "DockerSupervisor", lambda _: cli)
    monkeypatch.setattr(observer, "_inspect", lambda *_: None)
    monkeypatch.setattr(observer, "_host_snapshot", lambda _: object())
    with pytest.raises(ProcessError if wrapped else type(error)) as caught:
        if wrapped:
            observer._observe(
                container_id="d" * 64,
                image_id="sha256:" + "b" * 64,
                leader_pid=100,
                pid=101,
                deadline=60,
            )
        else:
            daemon_top.read_top("d" * 64, deadline=60)
    report = _diagnostic()._queue_failure(caught.value)
    assert report["observer_failure"] == expected
    assert report["family"] == (
        "process" if wrapped else "timeout" if expected == "deadline" else "other"
    )
    assert "private" not in json.dumps(report) and "d" * 64 not in json.dumps(report)
    assert factory.call_count == 1
    connection.__exit__.assert_called_once()
    cli._command.assert_not_called()


def test_untrusted_transport_name_and_timeout_context_are_not_deadline(monkeypatch):
    from types import FunctionType

    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.rdagent_operational import daemon_identity as observer
    from runtimes.research_supervisor import daemon_top

    def spoof():
        raise TimeoutError("private-payload")

    fake = FunctionType(
        spoof.__code__.replace(co_name="read_top"),
        {"__name__": daemon_top.__name__},
    )
    with pytest.raises(TimeoutError) as caught:
        fake()
    assert "observer_failure" not in _diagnostic()._queue_failure(caught.value)

    monkeypatch.setattr(observer, "time", SimpleNamespace(monotonic=lambda: 10))
    monkeypatch.setattr(observer, "DockerSupervisor", lambda _: object())
    monkeypatch.setattr(observer, "_inspect", lambda *_: None)
    monkeypatch.setattr(observer, "_host_snapshot", lambda _: object())
    monkeypatch.setattr(observer, "read_top", lambda *_, **__: fake())
    with pytest.raises(ProcessError) as caught:
        observer._observe(
            container_id="d" * 64,
            image_id="sha256:" + "b" * 64,
            leader_pid=100,
            pid=101,
            deadline=60,
        )
    report = _diagnostic()._queue_failure(caught.value)
    assert report["observer_failure"] == "unknown"
    assert "private" not in json.dumps(report)
