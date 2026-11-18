"""Offline contracts and child-cleanup tests; no PostgreSQL/native claims."""

import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/integration/test_workflow_heartbeat_admission_candidate.py"


def test_original_crossed_order_baseline_is_byte_unchanged():
    original = ROOT / "tests/integration/test_workflow_heartbeat_lock_order.py"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == (
        "3762d63a93527c240647e5ef5ef49470973822ea2ba8982167a09c5263263680"
    )


def test_actual_claim_observed_expiry_and_no_runtime_override():
    source = SOURCE.read_text()
    for required in (
        '"031_workflow_structured_stage", "032g_schema_keyset_cost"',
        "MINEAGENT_HEARTBEAT_ADMISSION_CANDIDATE_REQUIRED",
        "with actual_environment(source, settings, monkeypatch) as env:",
        "wid = create_workflow(env)",
        "drive_one(",
        "claim_configured_run(",
        "assert env.settings.lease_seconds == 120",
        "started, wall = monotonic(), utcnow()",
        "SELECT a.lease_expires_at",
        "a.lease_token=:token",
        "r.active_attempt_id=a.id",
        "observation_started_utc=wall",
        "observation_started_monotonic=started",
        "assert env.rd_calls == env.oe_calls == []",
        "assert lease.thread.ident is None",
    ):
        assert required in source
    assert source.index("started, wall =") < source.index("SELECT a.lease_expires_at")
    for forbidden in (
        "lease.start(",
        "run_bridge(",
        "drain(",
        "run_worker(",
        "render_candidate_sql",
        "DISABLE TRIGGER",
        "CREATE FUNCTION",
        "CREATE TRIGGER",
        "INSERT INTO",
        "statement_timeout=",
        "monkeypatch.setattr(AttemptLease",
        "monkeypatch.setattr(Session",
    ):
        assert forbidden not in source


def test_original_operations_commit_inside_gate_and_live_fence_is_not_replaced():
    source = SOURCE.read_text()
    functions = {
        n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
    }
    operation = functions["_operation"]
    assert len(operation.body) == 1 and isinstance(operation.body[0], ast.With)
    wrapper = operation.body[0]
    assert "state.gate.transaction" in ast.unparse(wrapper.items[0].context_expr)
    assert "state.lease.pulse(force=True)" in ast.unparse(wrapper)
    assert "neutral_transaction(state.env.factory, state.fence)" in ast.unparse(wrapper)
    assert "before_commit" in source and "after_commit" in source
    assert '"Candidate released before original commit"' in source
    assert source.count("UPDATE execution_attempts SET") == 2
    assert "lease_expires_at=clock_timestamp()-interval '1 second'" in source
    assert "lease_token=:token" in source
    assert 'errors[0].code != "attempt_fenced"' in source
    assert "_require_fenced_failure(errors)" in source
    assert "state.lease.lost.is_set() and state.lease.cancelled.is_set()" in source
    assert "_require_unchanged(state.source, changed)" in source
    assert "assert state.gate._cutoff == cutoff" in source
    assert 'changed == {"execution_attempts", "worker_heartbeats"}' in source


def test_external_holder_is_a_separate_owned_process_with_bounded_closed_ipc():
    source = SOURCE.read_text()
    for required in (
        'multiprocessing.get_context("spawn")',
        "context.Process(",
        "target=_external_owner",
        "args=(state.database_url, state.fence.owner_id, child)",
        "legacy.DATABASE_NAME.fullmatch",
        "source = legacy._engine(database_url)",
        'channel.send_bytes(b"ready")',
        'channel.recv_bytes(16) != b"release"',
        'channel.send_bytes(b"released")',
        'channel.send_bytes(b"failed")',
        "process.terminate()",
        "process.kill()",
        "process.join(timeout=5)",
        "assert not forced and not alive and exitcode == 0 and released",
        'caught.value.code == "neutral_busy"',
        "_owner_busy(caught.value)",
    ):
        assert required in source
    assert "print(" not in source and "os.kill" not in source
    holder = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_external_owner"
    )
    assert "gate" not in ast.unparse(holder)


@pytest.mark.parametrize("mode", ["ok", "release_pipe_broken", "child_stalled"])
def test_owned_process_cleanup_even_if_release_fails(monkeypatch, mode):
    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    spec = importlib.util.spec_from_file_location(
        "admission_process_cleanup_test", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Pipe:
        def __init__(self):
            self.messages = [b"ready", b"released"]
            self.closed = False

        def poll(self, timeout):
            return True

        def recv_bytes(self, maximum):
            assert maximum == 16
            return self.messages.pop(0)

        def send_bytes(self, message):
            assert message == b"release"
            calls.append("release")
            if mode == "release_pipe_broken":
                raise BrokenPipeError("private connection detail")

        def close(self):
            self.closed = True

    class Process:
        def __init__(self):
            self.alive = False
            self.exitcode = 0

        def start(self):
            calls.append("start")
            self.alive = mode == "child_stalled"

        def join(self, *, timeout):
            assert timeout == 5
            calls.append("join")

        def is_alive(self):
            return self.alive

        def terminate(self):
            calls.append("terminate")
            self.alive = False

        def close(self):
            calls.append("process-close")

    parent, child, process = Pipe(), Pipe(), Process()

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is True
            return parent, child

        def Process(self, *, target, args, daemon):
            assert target is module._external_owner and daemon is True
            assert args == ("private database url", "private owner", child)
            return process

    monkeypatch.setattr(module.multiprocessing, "get_context", lambda name: Context())
    state = SimpleNamespace(
        database_url="private database url",
        fence=SimpleNamespace(owner_id="private owner"),
    )
    if mode == "ok":
        with module._held_external_owner(state):
            calls.append("body")
    else:
        with pytest.raises(AssertionError), module._held_external_owner(state):
            calls.append("body")
    assert parent.closed and child.closed
    assert "join" in calls and "process-close" in calls
    assert ("terminate" in calls) is (mode == "child_stalled")


def test_unexpected_worker_failure_is_not_rendered(monkeypatch):
    from sqlalchemy.exc import DBAPIError

    from backend.app.services import DomainError

    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    spec = importlib.util.spec_from_file_location(
        "admission_private_failure_test", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private = "PRIVATE_LEASE_TOKEN_AND_SQL"
    failures = (
        [],
        [DBAPIError(private, {"token": private}, RuntimeError(private), False)],
        [DomainError(private, private)],
        [
            DomainError("attempt_fenced", private),
            DomainError("attempt_fenced", private),
        ],
    )
    for errors in failures:
        with pytest.raises(AssertionError) as caught:
            module._require_fenced_failure(errors)
        assert str(caught.value) == "Expected one original live-fence rejection"
        assert caught.value.__suppress_context__ is True
    module._require_fenced_failure([DomainError("attempt_fenced", private)])
