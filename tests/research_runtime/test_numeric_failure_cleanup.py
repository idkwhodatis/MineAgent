"""Bounded failure-path doubles, not actual Docker/PG containment acceptance."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.numeric_supervisor import (
    NumericDockerClient,
    NumericSupervisor,
)
from tests.research_runtime.test_numeric_process_boundary import journal


@pytest.mark.parametrize("failure", ["exited_race", "permission", "wait_timeout"])
def test_cli_finalizer_closes_pipe_and_sanitizes_failures(monkeypatch, failure):
    read, write = os.pipe()
    os.close(write)
    stream = os.fdopen(read, "rb")
    waits = []

    def wait(*, timeout):
        waits.append(timeout)
        if len(waits) == 2 and failure == "wait_timeout":
            raise subprocess.TimeoutExpired("synthetic-cli", timeout)
        return 0

    def kill(*_args):
        if failure == "exited_race":
            raise ProcessLookupError
        if failure == "permission":
            raise PermissionError

    process = SimpleNamespace(stdout=stream, pid=999999, wait=wait, poll=lambda: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(os, "killpg", kill)
    client = NumericDockerClient()
    if failure == "exited_race":
        assert client._command(["synthetic-no-Docker"]) == (0, b"")
    else:
        with pytest.raises(ProcessError, match="command_cleanup"):
            client._command(["synthetic-no-Docker"])
    assert stream.closed
    assert len(waits) <= 2
    if len(waits) == 2:
        assert 0 < waits[-1] <= 1


@pytest.mark.parametrize("status", ["created", "exited", "running_observation_lost"])
def test_exact_unobserved_container_cleanup_never_invents_stop(
    monkeypatch, tmp_path, status
):
    admission, _launch, _identity, _stop, _extraction, _, path = journal(tmp_path)
    (path / "control" / "identity.json").unlink()
    (path / "control" / "stop.json").unlink()
    state = {
        "Id": "c" * 64,
        "State": {
            "Running": status == "running_observation_lost",
            "Status": status,
            "StartedAt": "0001-01-01T00:00:00Z"
            if status == "created"
            else "2026-01-01T00:00:00Z",
        },
    }
    commands, verified = [], []

    class Client:
        deadline = None
        present = True

        def _inspect(self, _name):
            return state if self.present else None

        def _command(self, args):
            commands.append(args)
            assert args == ["rm", "--force", state["Id"]]
            self.present = False
            return 0, b""

    supervisor = NumericSupervisor(admission.image_id)
    supervisor.client = Client()
    monkeypatch.setattr(supervisor, "verified_inventory", lambda _role: {})
    monkeypatch.setattr(
        supervisor, "_verify", lambda *args: verified.append(args) or state
    )

    def missing_identity(*_args):
        raise ProcessError("numeric_process_kernel_identity")

    monkeypatch.setattr(supervisor, "_observe_identity", missing_identity)
    with pytest.raises(
        ProcessError,
        match="created_cleanup_uncertain"
        if status == "created"
        else "identity_unobserved",
    ):
        supervisor.stop_and_verify(tmp_path, admission)
    assert len(verified) == 1 and len(commands) == 1
    assert not (path / "control" / "identity.json").exists()
    assert not (path / "control" / "stop.json").exists()
    # A second read after container deletion still cannot invent stop evidence.
    with pytest.raises(ProcessError, match="identity_unobserved"):
        supervisor.stop_and_verify(tmp_path, admission)
    assert len(commands) == 1


def test_unverified_container_is_not_deleted(monkeypatch, tmp_path):
    admission, _, _, _, _, _, _ = journal(tmp_path)
    supervisor = NumericSupervisor(admission.image_id)
    commands = []
    supervisor.client = SimpleNamespace(
        deadline=None,
        _inspect=lambda _n: {"Id": "c" * 64},
        _command=lambda args: commands.append(args),
    )
    monkeypatch.setattr(supervisor, "verified_inventory", lambda _role: {})

    def reject(*_args):
        raise ProcessError("numeric_process_runtime_identity")

    monkeypatch.setattr(supervisor, "_verify", reject)
    with pytest.raises(ProcessError, match="runtime_identity"):
        supervisor.stop_and_verify(tmp_path, admission)
    assert commands == []


def test_launch_failure_is_not_hidden_by_missing_identity_cleanup(
    monkeypatch, tmp_path
):
    from runtimes.research_supervisor.numeric_supervisor import _mint_numeric_authority
    from tests.research_runtime.test_numeric_issuer import bound

    intent, inputs = bound()
    authority = _mint_numeric_authority(intent, inputs, {})
    supervisor = NumericSupervisor(intent.admission.image_id)
    original = ProcessError("numeric_process_kernel_identity")
    attempts = []

    def create_failure(*_a, **_k):
        raise original

    def stop_failure(*_a, **kwargs):
        attempts.append(kwargs)
        raise ProcessError("numeric_process_identity_unobserved")

    monkeypatch.setattr(supervisor.client, "_command", create_failure)
    monkeypatch.setattr(supervisor, "stop_and_verify", stop_failure)
    with pytest.raises(ProcessError) as raised:
        supervisor.execute(
            tmp_path, authority, on_identity=lambda _: pytest.fail("no identity")
        )
    assert raised.value is original
    assert len(attempts) == 2
    assert original.__notes__ == [
        "Numeric physical containment unconfirmed: ProcessError"
    ]
    assert list(tmp_path.rglob("identity.json")) == []
    assert list(tmp_path.rglob("stop.json")) == []


@pytest.mark.parametrize("failed_execution", [False, True])
def test_containment_persistence_failure_never_creates_success_or_masks_cause(
    monkeypatch, tmp_path, failed_execution
):
    from contextlib import contextmanager

    from tests.research_runtime.test_numeric_issuer import bound
    from workers.research import numeric_process as worker

    intent, inputs = bound()
    original = ProcessError("numeric_process_kernel_identity")
    commits = []

    @contextmanager
    def transaction(*_a, **_kw):
        yield SimpleNamespace(commit=lambda: commits.append(True))

    def execute(*_a, **_kw):
        if failed_execution:
            raise original

    def missing(*_a, **_kw):
        raise FileNotFoundError("identity.json")

    monkeypatch.setattr(worker, "neutral_transaction", transaction)
    monkeypatch.setattr(worker, "check_numeric_claim", lambda *_a: None)
    monkeypatch.setattr(
        worker, "NumericSupervisor", lambda _image: SimpleNamespace(execute=execute)
    )
    monkeypatch.setattr(worker, "record_numeric_stopped", lambda *_a, **_kw: None)
    monkeypatch.setattr(worker, "record_numeric_containment", missing)
    with pytest.raises(
        ProcessError if failed_execution else FileNotFoundError
    ) as raised:
        worker._execute_claimed_numeric_unit(
            transaction, object(), tmp_path, intent, input_bytes=inputs, inventory={}
        )
    assert commits == []
    if failed_execution:
        assert raised.value is original
        assert original.__notes__ == [
            "Numeric containment persistence unconfirmed: FileNotFoundError"
        ]
